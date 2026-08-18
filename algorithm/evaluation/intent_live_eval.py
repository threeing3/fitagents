"""Evaluate rule, direct live-model, and hybrid intent decisions on fixed test data."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from algorithm.evaluation.intent_eval_core import intent_checks
from fast_api.app.core.config import get_settings
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.intent_decision_engine import IntentDecisionEngine
from fast_api.app.services.llm_intent_classifier import LLMIntentClassifier
from fast_api.app.services.model_provider import ModelProvider

EvalMode = Literal["rule_only", "deepseek_all_with_rule_safety", "hybrid"]
_checks = intent_checks


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[round((len(ordered) - 1) * fraction)], 2)


async def evaluate_rows(
    rows: list[dict[str, Any]],
    mode: EvalMode,
    *,
    limit: int | None = None,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    selected = rows[:limit] if limit else rows
    router = IntentRouter()
    provider = ModelProvider()
    classifier = LLMIntentClassifier(provider, router)
    engine = IntentDecisionEngine(provider, router)
    details: list[dict[str, Any]] = []
    component_passed: Counter[str] = Counter()
    category_totals: Counter[str] = Counter()
    category_passed: Counter[str] = Counter()
    latencies: list[float] = []
    token_totals: Counter[str] = Counter()
    fallback_reasons: Counter[str] = Counter()

    for index, row in enumerate(selected):
        message = str(row.get("input") or row.get("user_message") or "")
        rule_decision = router.analyze(message)
        trace: dict[str, Any] = {"attempted": False, "succeeded": False}
        started = time.perf_counter()
        if mode == "rule_only":
            decision = rule_decision
        elif mode == "deepseek_all_with_rule_safety":
            decision, trace = await classifier.refine_with_trace(
                message, rule_decision, force_refine=True
            )
        else:
            engine_result = await engine.decide(message)
            decision = engine_result.decision.to_legacy()
            provenance = engine_result.decision.provenance
            trace = {
                "attempted": provenance.get("model_attempted", False),
                "succeeded": provenance.get("model_succeeded", False),
                "fallback_reason": provenance.get("model_fallback_reason"),
                "usage": provenance.get("model_usage") or {},
            }
        latency = (time.perf_counter() - started) * 1000
        latencies.append(latency)
        checks = _checks(row, decision)
        for name, passed in checks.items():
            component_passed[name] += int(passed)
        exact = all(checks.values())
        category = str(
            row.get("category")
            or row.get("expected_primary_intent")
            or row.get("expected_intent")
            or "unknown"
        )
        category_totals[category] += 1
        category_passed[category] += int(exact)
        usage_value = trace.get("usage")
        usage: dict[str, Any] = dict(usage_value) if isinstance(usage_value, dict) else {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            token_totals[key] += int(usage.get(key) or 0)
        if trace.get("fallback_reason"):
            fallback_reasons[str(trace["fallback_reason"])] += 1
        details.append(
            {
                "case_id": row.get("name") or row.get("case_id") or f"row-{index}",
                "category": category,
                "checks": checks,
                "exact_pass": exact,
                "expected_primary_intent": row.get("expected_primary_intent")
                or row.get("expected_intent"),
                "actual": decision.to_dict(),
                "model_trace": trace,
                "latency_ms": round(latency, 2),
            }
        )

    count = len(details)
    exact_count = sum(int(item["exact_pass"]) for item in details)
    failures = [item for item in details if not item["exact_pass"]]
    settings = get_settings()
    summary = {
        "experiment_id": f"intent_{mode}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "provider": settings.llm_provider if mode != "rule_only" else "none",
        "model": settings.chat_model if mode != "rule_only" else "intent_rules_v2",
        "source": "fixed_test_eval",
        "training_eligible": False,
        "deterministic_safety_merge": mode != "rule_only",
        "cases": count,
        "exact_passed": exact_count,
        "exact_pass_rate": round(exact_count / count, 4) if count else 0.0,
        "component_scores": {
            name: round(passed / count, 4) if count else 0.0
            for name, passed in sorted(component_passed.items())
        },
        "model_calls": sum(int(item["model_trace"].get("attempted", False)) for item in details),
        "model_successes": sum(
            int(item["model_trace"].get("succeeded", False)) for item in details
        ),
        "fallback_reasons": dict(fallback_reasons),
        "tokens": dict(token_totals),
        "token_usage_available": not (
            mode == "hybrid"
            and any(item["model_trace"].get("succeeded", False) for item in details)
            and token_totals["total_tokens"] == 0
        ),
        "estimated_cost_usd": (
            round(
                token_totals["input_tokens"] * input_usd_per_million / 1_000_000
                + token_totals["output_tokens"] * output_usd_per_million / 1_000_000,
                6,
            )
            if input_usd_per_million is not None
            and output_usd_per_million is not None
            and not (
                mode == "hybrid"
                and any(item["model_trace"].get("succeeded", False) for item in details)
                and token_totals["total_tokens"] == 0
            )
            else None
        ),
        "pricing_snapshot": (
            {
                "input_usd_per_million": input_usd_per_million,
                "output_usd_per_million": output_usd_per_million,
                "cache_assumption": "cache_miss",
            }
            if input_usd_per_million is not None and output_usd_per_million is not None
            else None
        ),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
        },
        "categories": [
            {
                "name": name,
                "cases": category_totals[name],
                "passed": category_passed[name],
                "pass_rate": round(category_passed[name] / category_totals[name], 4),
            }
            for name in sorted(category_totals)
        ],
        "failure_count": len(failures),
        "failure_examples": [
            {
                "case_id": item["case_id"],
                "category": item["category"],
                "checks": item["checks"],
                "expected_primary_intent": item["expected_primary_intent"],
                "actual_primary_intent": item["actual"]["primary_intent"],
            }
            for item in failures[:12]
        ],
    }
    return summary, details


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run fixed intent evaluation paths")
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--mode",
        choices=["rule_only", "deepseek_all_with_rule_safety", "hybrid"],
        required=True,
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--details-output", type=Path, required=True)
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    args = parser.parse_args()
    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    summary, details = await evaluate_rows(
        rows,
        args.mode,
        limit=args.limit,
        input_usd_per_million=args.input_usd_per_million,
        output_usd_per_million=args.output_usd_per_million,
    )
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.details_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.details_output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in details),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
