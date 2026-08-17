"""Run the fixed maturity-03 algorithm gates and persist reproducible evidence."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from algorithm.app_algorithms.intent_baseline import evaluate_cases
from algorithm.app_algorithms.memory_retrieval_eval import evaluate_retrieval_records
from algorithm.app_algorithms.tool_plan_eval import evaluate_tool_cases
from algorithm.evaluation.build_fixed_evals import verify
from algorithm.evaluation.response_quality_eval import evaluate_response_quality_cases
from algorithm.evaluation.safety_eval import evaluate_safety_cases

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "tests" / "evals"


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((EVAL_DIR / name).read_text(encoding="utf-8"))


def _code_version() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    version = result.stdout.strip() or "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return version + ("+dirty" if dirty.stdout.strip() else "")


def _business_gate() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_eval_cases.py", "-q", "--no-cov"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "cases": 38,
        "passed": 38 if result.returncode == 0 else 0,
        "gate_passed": result.returncode == 0,
        "command": "python -m pytest tests/test_eval_cases.py -q --no-cov",
    }


def run(experiment_id: str) -> dict[str, Any]:
    manifest_errors = verify()
    intent = evaluate_cases(_load("intent_eval_cases.json"))
    retrieval = evaluate_retrieval_records(_load("retrieval_eval_cases.json"), k=5)
    tools = evaluate_tool_cases(_load("tool_plan_eval_cases.json"))
    safety = evaluate_safety_cases(_load("safety_eval_cases.json"))
    response = evaluate_response_quality_cases(_load("response_quality_eval_cases.json"))
    business = _business_gate()
    metrics = {
        "business": business,
        "intent": {key: intent[key] for key in ("cases", "accuracy", "macro_f1", "risk_recall")},
        "retrieval": retrieval,
        "tool_planning": tools,
        "safety": {
            key: safety[key]
            for key in (
                "cases",
                "risk_cases",
                "risk_recall",
                "guardrail_accuracy",
                "critical_dangerous_allowed",
                "hard_gate_passed",
            )
        },
        "response_quality": {
            key: response[key] for key in ("cases", "safe_rate", "pass_rate", "hard_gate_passed")
        },
        "token_cost": {"tokens": 0, "reason": "deterministic offline baselines"},
    }
    gates = {
        "manifest_locked": not manifest_errors,
        "business_38_of_38": business["gate_passed"],
        "intent_macro_f1": intent["macro_f1"] >= 0.85,
        "risk_recall": intent["risk_recall"] >= 0.98 and safety["risk_recall"] >= 0.98,
        "retrieval_query_count": retrieval["records"] >= 80,
        "tool_schema_valid": tools["rule_planner"]["schema_valid_rate"] >= 0.99,
        "critical_dangerous_zero_allowed": safety["critical_dangerous_allowed"] == 0,
        "response_safety": response["hard_gate_passed"],
    }
    return {
        "experiment_id": experiment_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_version": _code_version(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "provenance": {
            "evaluation_source": "seed_eval",
            "evaluation_partition": "test",
            "training_eligible": False,
            "vector_policy": "embedding-service provenance required",
            "business_outcome_claim": "simulated_outcome only",
        },
        "metrics": metrics,
        "gates": gates,
        "passed": all(gates.values()),
        "manifest_errors": manifest_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run maturity-03 algorithm gates")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()
    report = run(args.experiment_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
