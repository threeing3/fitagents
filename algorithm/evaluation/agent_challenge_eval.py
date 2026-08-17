"""Evaluate rule-first Agent routing on a fixed high-difficulty challenge set."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from algorithm.app_algorithms.tool_plan_eval import build_rule_plan
from fast_api.app.services.intent_decision import IntentRouter

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def evaluate_agent_challenges(rows: list[dict[str, Any]]) -> dict[str, Any]:
    router = IntentRouter()
    category_totals: dict[str, int] = defaultdict(int)
    category_passed: dict[str, int] = defaultdict(int)
    component_totals: dict[str, int] = defaultdict(int)
    details: list[dict[str, Any]] = []

    for row in rows:
        decision = router.analyze(str(row["user_message"]))
        plan = build_rule_plan(str(row["user_message"]), router=router)
        required_secondary = set(row.get("required_secondary_intents") or [])
        required_tools = set(row.get("required_tools") or [])
        checks = {
            "primary_intent": decision.primary_intent == row["expected_primary_intent"],
            "secondary_intents": required_secondary.issubset(set(decision.secondary_intents)),
            "risk_level": RISK_ORDER.get(decision.risk_level, -1)
            >= RISK_ORDER.get(str(row.get("minimum_risk_level") or "low"), 0),
            "clarification": decision.needs_clarification is bool(row["expected_clarification"]),
            "required_tools": required_tools.issubset(set(plan["selected_tools"])),
        }
        passed = all(checks.values())
        for name, succeeded in checks.items():
            component_totals[name] += int(succeeded)
        category = str(row["category"])
        category_totals[category] += 1
        category_passed[category] += int(passed)
        details.append(
            {
                "case_id": row["case_id"],
                "category": category,
                "passed": passed,
                "checks": checks,
                "expected": {
                    "primary_intent": row["expected_primary_intent"],
                    "secondary_intents": row.get("required_secondary_intents") or [],
                    "minimum_risk_level": row.get("minimum_risk_level"),
                    "clarification": row["expected_clarification"],
                    "required_tools": row.get("required_tools") or [],
                },
                "actual": {
                    "primary_intent": decision.primary_intent,
                    "secondary_intents": decision.secondary_intents,
                    "risk_level": decision.risk_level,
                    "clarification": decision.needs_clarification,
                    "selected_tools": plan["selected_tools"],
                },
                "user_message": row["user_message"],
            }
        )

    passed_count = sum(int(item["passed"]) for item in details)
    categories = [
        {
            "name": name,
            "cases": category_totals[name],
            "passed": category_passed[name],
            "pass_rate": round(category_passed[name] / category_totals[name], 4),
        }
        for name in sorted(category_totals)
    ]
    failures = [item for item in details if not item["passed"]]
    return {
        "experiment_id": "agent_challenge_v1",
        "source": "challenge_eval",
        "partition": "test",
        "training_eligible": False,
        "cases": len(details),
        "passed": passed_count,
        "pass_rate": round(passed_count / len(details), 4) if details else 0.0,
        "component_scores": {
            name: round(passed / len(details), 4) if details else 0.0
            for name, passed in sorted(component_totals.items())
        },
        "categories": categories,
        "failure_count": len(failures),
        "failure_examples": failures[:12],
        "limitations": [
            "Template-variant challenge set; not production-distribution evidence.",
            "Evaluates deterministic routing and action policy, not final LLM response quality.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the Agent challenge set")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = evaluate_agent_challenges(rows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
