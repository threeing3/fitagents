"""Deterministic response-quality checks with safety as a non-compensable gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fast_api.app.core.guardrails import run_guardrails

ACTION_TERMS = ("建议", "可以", "先", "再", "停止", "记录", "consult", "start", "stop")


def evaluate_response_quality_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    for case in cases:
        response = str(case.get("response") or "")
        expected_terms = [str(term) for term in case.get("expected_terms") or []]
        matched_terms = [term for term in expected_terms if term.lower() in response.lower()]
        relevance = len(matched_terms) / len(expected_terms) if expected_terms else 1.0
        actionable = any(term.lower() in response.lower() for term in ACTION_TERMS)
        guardrail = run_guardrails(response, user_message=str(case.get("user_message") or ""))
        safe = bool(guardrail.passed)
        quality_score = round(0.55 * relevance + 0.25 * float(actionable) + 0.20, 4)
        passed = safe and relevance >= 0.5 and actionable
        details.append(
            {
                "case_id": case.get("case_id"),
                "safe": safe,
                "relevance": relevance,
                "actionable": actionable,
                "quality_score": quality_score,
                "passed": passed,
            }
        )
    count = len(details)
    safe_rate = sum(row["safe"] for row in details) / count if count else 0.0
    pass_rate = sum(row["passed"] for row in details) / count if count else 0.0
    return {
        "cases": count,
        "safe_rate": safe_rate,
        "pass_rate": pass_rate,
        "hard_gate_passed": all(row["safe"] for row in details),
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fixed response-quality evaluation")
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/evals/response_quality_eval_cases.json")
    )
    args = parser.parse_args()
    report = evaluate_response_quality_cases(json.loads(args.cases.read_text(encoding="utf-8")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["hard_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
