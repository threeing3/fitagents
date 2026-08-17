"""Metrics for structured tool plans and host-executable traces."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from fast_api.app.services.intent_decision import IntentRouter

RULE_TOOL_SEQUENCES: dict[str, list[str]] = {
    "injury_or_risk": ["context.build", "safety.check", "recovery.evaluate"],
    "training_plan": [
        "context.build",
        "memory.search",
        "knowledge.retrieve",
        "plan.generate",
        "plan.validate",
    ],
    "progression_decision": [
        "context.build",
        "memory.search",
        "recovery.evaluate",
        "knowledge.retrieve",
    ],
    "nutrition_log": ["context.build", "nutrition.log.write"],
    "training_log": ["context.build", "training.log.write"],
    "nutrition_advice": ["context.build", "nutrition.estimate", "knowledge.retrieve"],
    "recovery_check": ["context.build", "recovery.evaluate"],
    "memory_query": ["memory.search"],
    "profile_update": ["profile.update"],
    "profile_correction": ["profile.correct"],
    "weekly_review": ["context.build", "review.weekly"],
    "monthly_review": ["context.build", "review.monthly"],
    "general_chat": ["context.build"],
}


def build_rule_plan(user_message: str, router: IntentRouter | None = None) -> dict[str, Any]:
    """Build the deterministic, host-executable tool baseline."""

    decision = (router or IntentRouter()).analyze(user_message)
    sequence = list(RULE_TOOL_SEQUENCES.get(decision.primary_intent, ["context.build"]))
    return {
        "intent": decision.primary_intent,
        "risk_level": decision.risk_level,
        "selected_tools": list(sequence),
        "tool_sequence": sequence,
        "plan_valid": True,
    }


def schema_valid(plan: Any) -> bool:
    if not isinstance(plan, dict):
        return False
    selected = plan.get("selected_tools")
    sequence = plan.get("tool_sequence")
    if not isinstance(selected, list) or not all(
        isinstance(item, str) and item for item in selected
    ):
        return False
    if not isinstance(sequence, list) or not all(
        isinstance(item, str) and item for item in sequence
    ):
        return False
    if len(set(selected)) != len(selected) or any(item not in selected for item in sequence):
        return False
    if "plan_valid" in plan and not isinstance(plan["plan_valid"], bool):
        return False
    if "risk_level" in plan and plan["risk_level"] not in {
        "low",
        "medium",
        "high",
        "critical",
        "unknown",
    }:
        return False
    return True


def tool_exact_match(predicted: list[str], expected: list[str]) -> bool:
    return predicted == expected


def tool_selection_exact_match(predicted: list[str], expected: list[str]) -> bool:
    return sorted(set(predicted)) == sorted(set(expected))


def schema_valid_rate(plans: list[Any]) -> float:
    return sum(schema_valid(plan) for plan in plans) / len(plans) if plans else 0.0


def tool_sequence_accuracy(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    correct = sum(
        tool_exact_match(
            record.get("predicted_sequence") or [], record.get("expected_sequence") or []
        )
        for record in records
    )
    return correct / len(records)


def tool_selection_accuracy(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    correct = sum(
        tool_selection_exact_match(
            record.get("predicted_tools") or [], record.get("expected_tools") or []
        )
        for record in records
    )
    return correct / len(records)


def unnecessary_tool_rate(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    unnecessary = sum(int(record.get("unnecessary_tools", 0)) for record in records)
    total = sum(max(1, int(record.get("tool_count", 0))) for record in records)
    return unnecessary / total


def evaluate_tool_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare a fixed chain with the rule planner; live LLM planning is explicit."""

    rule_plans: list[dict[str, Any]] = []
    fixed_plans: list[dict[str, Any]] = []
    rule_records: list[dict[str, Any]] = []
    fixed_records: list[dict[str, Any]] = []
    latencies: list[float] = []
    fixed_sequence = ["context.build", "knowledge.retrieve"]
    for case in cases:
        expected = list(case.get("expected_sequence") or [])
        started = time.perf_counter()
        plan = build_rule_plan(str(case.get("user_message") or ""))
        latencies.append((time.perf_counter() - started) * 1000)
        predicted = list(plan["tool_sequence"])
        rule_plans.append(plan)
        rule_records.append(
            {
                "predicted_tools": plan["selected_tools"],
                "expected_tools": list(case.get("expected_tools") or expected),
                "predicted_sequence": predicted,
                "expected_sequence": expected,
                "unnecessary_tools": len(set(predicted) - set(expected)),
                "tool_count": len(predicted),
            }
        )
        fixed_records.append(
            {
                "predicted_tools": fixed_sequence,
                "expected_tools": list(case.get("expected_tools") or expected),
                "predicted_sequence": fixed_sequence,
                "expected_sequence": expected,
                "unnecessary_tools": len(set(fixed_sequence) - set(expected)),
                "tool_count": len(fixed_sequence),
            }
        )
        fixed_plans.append(
            {
                "selected_tools": list(fixed_sequence),
                "tool_sequence": list(fixed_sequence),
                "plan_valid": True,
                "risk_level": "unknown",
            }
        )
    ordered_latency = sorted(latencies)

    def percentile(fraction: float) -> float:
        if not ordered_latency:
            return 0.0
        index = round((len(ordered_latency) - 1) * fraction)
        return round(ordered_latency[index], 4)

    def metrics(records: list[dict[str, Any]], plans: list[Any] | None = None) -> dict[str, Any]:
        return {
            "tool_selection_exact_match": tool_selection_accuracy(records),
            "tool_sequence_accuracy": tool_sequence_accuracy(records),
            "schema_valid_rate": schema_valid_rate(plans or []),
            "unnecessary_tool_rate": unnecessary_tool_rate(records),
        }

    rule_metrics = metrics(rule_records, rule_plans)
    rule_metrics.update({"p50_latency_ms": percentile(0.50), "p95_latency_ms": percentile(0.95)})
    return {
        "cases": len(cases),
        "fixed_chain": metrics(fixed_records, fixed_plans),
        "rule_planner": rule_metrics,
        "llm_planner": {
            "available": False,
            "status": "not evaluated without an explicitly configured model",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic tool planning")
    parser.add_argument("--cases", type=Path, default=Path("tests/evals/tool_plan_eval_cases.json"))
    args = parser.parse_args()
    report = evaluate_tool_cases(json.loads(args.cases.read_text(encoding="utf-8")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
