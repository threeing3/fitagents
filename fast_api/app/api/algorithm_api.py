"""Public, sanitized algorithm maturity summary for the Algorithm Lab."""

from fastapi import APIRouter

algorithm_router = APIRouter(prefix="/v1/algorithm", tags=["algorithm-lab"])


@algorithm_router.get("/summary")
def algorithm_summary() -> dict:
    """Return fixed, source-labelled evidence and never raw user examples."""

    return {
        "release_stage": "maturity_02_product",
        "disclaimer": "Baseline evidence only; no online business uplift is claimed.",
        "datasets": [
            {"name": "business_fixed", "size": 38, "source": "rule_and_curated", "status": "fixed"},
            {"name": "intent_baseline", "size": 6, "source": "curated", "status": "expanding"},
            {"name": "retrieval_baseline", "size": 2, "source": "curated", "status": "expanding"},
            {"name": "sft_seed", "size": 31, "source": "mixed_non_expert", "status": "seed_only"},
            {
                "name": "preference_reviewed",
                "size": 0,
                "source": "human_review",
                "status": "blocked",
            },
        ],
        "metrics": [
            {"name": "backend_tests", "value": 551, "unit": "passed", "source": "local_gate"},
            {"name": "business_fixed_pass", "value": 27, "total": 38, "source": "fixed_eval"},
            {"name": "coverage", "value": 66.07, "unit": "percent", "source": "local_gate"},
        ],
        "business_outcomes": {"label": "simulated_outcome", "online_claim": False},
        "dpo": {"enabled": False, "minimum_reviewed_pairs": 150, "current_reviewed_pairs": 0},
    }
