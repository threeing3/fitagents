"""Public, sanitized algorithm maturity evidence for the Algorithm Lab."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

algorithm_router = APIRouter(prefix="/v1/algorithm", tags=["algorithm-lab"])
REPORT_PATH = (
    Path(__file__).resolve().parents[3]
    / "algorithm"
    / "evaluation"
    / "reports"
    / "maturity_03_baseline.summary.json"
)


def _report() -> dict:
    if not REPORT_PATH.exists():
        raise HTTPException(status_code=503, detail="Algorithm report is not available.")
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


@algorithm_router.get("/summary")
def algorithm_summary() -> dict:
    """Return fixed, source-labelled evidence and never raw user examples."""

    report = _report()
    metrics = report["metrics"]
    return {
        "release_stage": "maturity_03_algorithms",
        "disclaimer": (
            "Fixed seed_eval and synthetic-factory evidence only; "
            "no online business uplift is claimed."
        ),
        "datasets": [
            {"name": "business_fixed", "size": 38, "source": "rule_and_curated", "status": "fixed"},
            {
                "name": "intent_fixed",
                "size": 120,
                "source": "seed_eval",
                "status": "fixed_test_only",
            },
            {
                "name": "retrieval_fixed",
                "size": 80,
                "source": "seed_eval",
                "status": "fixed_test_only",
            },
            {
                "name": "tool_plan_fixed",
                "size": 200,
                "source": "seed_eval",
                "status": "fixed_test_only",
            },
            {"name": "safety_fixed", "size": 150, "source": "seed_eval", "status": "hard_gate"},
            {
                "name": "response_quality_fixed",
                "size": 100,
                "source": "seed_eval",
                "status": "hard_gate",
            },
            {
                "name": "sft_factory_train",
                "size": 960,
                "source": "synthetic",
                "status": "factory_validated",
            },
            {
                "name": "preference_reviewed",
                "size": 0,
                "source": "human_review",
                "status": "blocked",
            },
        ],
        "metrics": [
            {"name": "business_fixed_pass", "value": 38, "total": 38, "source": "fixed_eval"},
            {
                "name": "intent_macro_f1",
                "value": round(metrics["intent"]["macro_f1"] * 100, 2),
                "unit": "percent",
                "source": "seed_eval",
            },
            {
                "name": "risk_recall",
                "value": round(metrics["safety"]["risk_recall"] * 100, 2),
                "unit": "percent",
                "source": "seed_eval",
            },
            {
                "name": "memory_recall_at_5",
                "value": round(
                    metrics["retrieval"]["strategies"]["hybrid"]["recall_at_k"] * 100, 2
                ),
                "unit": "percent",
                "source": "seed_eval",
            },
            {
                "name": "tool_sequence_accuracy",
                "value": round(
                    metrics["tool_planning"]["rule_planner"]["tool_sequence_accuracy"] * 100, 2
                ),
                "unit": "percent",
                "source": "seed_eval",
            },
            {
                "name": "schema_valid_rate",
                "value": round(
                    metrics["tool_planning"]["rule_planner"]["schema_valid_rate"] * 100, 2
                ),
                "unit": "percent",
                "source": "seed_eval",
            },
            {
                "name": "critical_dangerous_allowed",
                "value": metrics["safety"]["critical_dangerous_allowed"],
                "unit": "count",
                "source": "seed_eval",
            },
        ],
        "retrieval": {
            "vector_status": metrics["retrieval"]["vector_status"],
            "bm25_recall_at_5": metrics["retrieval"]["strategies"]["bm25"]["recall_at_k"],
            "hybrid_recall_at_5": metrics["retrieval"]["strategies"]["hybrid"]["recall_at_k"],
        },
        "training_factory": {
            "source": "synthetic",
            "outcome_label": "simulated_outcome",
            "split": {"train": 960, "validation": 120, "test": 120},
            "user_split_leaks": 0,
        },
        "business_outcomes": {"label": "simulated_outcome", "online_claim": False},
        "dpo": {"enabled": False, "minimum_reviewed_pairs": 150, "current_reviewed_pairs": 0},
    }


@algorithm_router.get("/experiments/{experiment_id}")
def algorithm_experiment(experiment_id: str) -> dict:
    """Return a reviewed report only; raw prompts and user traces never leave storage."""

    report = _report()
    if experiment_id != report.get("experiment_id"):
        raise HTTPException(status_code=404, detail="Published experiment was not found.")
    return report
