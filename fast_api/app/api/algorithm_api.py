"""Public, sanitized algorithm maturity evidence for the Algorithm Lab."""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.core.config import get_settings
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.services.agent_trace_analysis import analyze_agent_run
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.intent_decision_engine import IntentDecisionEngine
from fast_api.app.services.model_provider import ModelProvider

algorithm_router = APIRouter(prefix="/v1/algorithm", tags=["algorithm-lab"])
REPORT_PATH = (
    Path(__file__).resolve().parents[3]
    / "algorithm"
    / "evaluation"
    / "reports"
    / "maturity_03_baseline.summary.json"
)
CHALLENGE_REPORT_PATH = (
    Path(__file__).resolve().parents[3]
    / "algorithm"
    / "evaluation"
    / "reports"
    / "agent_challenge_v1.summary.json"
)
INTENT_RELEASE_REPORT_PATH = (
    Path(__file__).resolve().parents[3]
    / "algorithm"
    / "evaluation"
    / "reports"
    / "intent_qwen3_4b_release.summary.json"
)


class AlgorithmCompareRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def _report() -> dict:
    if not REPORT_PATH.exists():
        raise HTTPException(status_code=503, detail="Algorithm report is not available.")
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def _intent_release() -> dict | None:
    if not INTENT_RELEASE_REPORT_PATH.exists():
        return None
    report = json.loads(INTENT_RELEASE_REPORT_PATH.read_text(encoding="utf-8"))
    if (
        report.get("schema_version") != "fitagent-public-intent-release/v1"
        or report.get("status") != "verified_offline"
    ):
        return None
    dataset = report.get("dataset") if isinstance(report.get("dataset"), dict) else {}
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    evidence = (
        report.get("evidence_sha256") if isinstance(report.get("evidence_sha256"), dict) else {}
    )
    claims = report.get("claims") if isinstance(report.get("claims"), dict) else {}
    return {
        "schema_version": report["schema_version"],
        "experiment_id": report.get("experiment_id"),
        "status": report["status"],
        "base_model": report.get("base_model"),
        "dataset": {key: dataset.get(key) for key in ("source", "training_eligible", "cases")},
        "metrics": {
            key: metrics.get(key)
            for key in (
                "base_exact_match",
                "adapter_exact_match",
                "adapter_schema_valid_rate",
                "adapter_risk_recall",
                "risk_cases",
                "latency_ms",
            )
        },
        "adapter_sha256": report.get("adapter_sha256"),
        "evidence_sha256": {
            key: evidence.get(key) for key in ("release_manifest", "base_report", "adapter_report")
        },
        "claims": {
            key: claims.get(key)
            for key in (
                "offline_evaluation_verified",
                "online_business_uplift",
                "real_user_outcome",
            )
        },
        "published_at": report.get("published_at"),
    }


@algorithm_router.get("/summary")
def algorithm_summary() -> dict:
    """Return fixed, source-labelled evidence and never raw user examples."""

    report = _report()
    metrics = report["metrics"]
    intent_release = _intent_release()
    adapter_configured = bool(get_settings().adapter_inference_url)
    adapter_status = (
        "verified_service_configured"
        if intent_release and adapter_configured
        else "verified_offline"
        if intent_release
        else "configured_unverified"
        if adapter_configured
        else "not_trained"
    )
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
        "intent_inference": {
            "architecture": "rules_then_qwen3_adapter_then_deepseek_fallback",
            "adapter_status": adapter_status,
            "adapter_model": "Qwen3-4B-QLoRA",
            "safety_authority": "deterministic_rules",
            "online_result_claimed": False,
            "verified_release": intent_release,
        },
    }


@algorithm_router.post("/compare")
async def algorithm_compare(
    body: AlgorithmCompareRequest,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Compare the rule baseline with the configured runtime path for one sanitized turn."""

    router = IntentRouter()
    rule = router.analyze(body.message)
    provider = ModelProvider(user_id=current_user.id, endpoint="algorithm_compare")
    result = await IntentDecisionEngine(provider, router).decide(body.message)
    provenance = result.decision.provenance
    return {
        "rule_baseline": {
            "primary_intent": rule.primary_intent,
            "risk_level": rule.risk_level,
            "confidence": rule.confidence,
        },
        "runtime_decision": {
            "primary_intent": result.decision.primary_intent,
            "secondary_intents": result.decision.secondary_intents,
            "risk_level": result.decision.risk_level,
            "confidence": result.decision.confidence.get("overall", 0),
        },
        "routing": {
            "final_source": provenance.get("final_source"),
            "local_model_status": provenance.get("local_model_status"),
            "local_model_used": bool(provenance.get("local_model_used")),
            "deepseek_used": bool(provenance.get("deepseek_used")),
            "safety_rule_applied": bool(provenance.get("rule_used")),
        },
        "latency_ms": result.decision.latency_ms,
        "disclaimer": "A live single-case comparison is not an offline evaluation result.",
    }


@algorithm_router.get("/experiments/{experiment_id}")
def algorithm_experiment(experiment_id: str) -> dict:
    """Return a reviewed report only; raw prompts and user traces never leave storage."""

    report = _report()
    if experiment_id != report.get("experiment_id"):
        raise HTTPException(status_code=404, detail="Published experiment was not found.")
    return report


@algorithm_router.get("/challenges/summary")
def algorithm_challenge_summary() -> dict:
    """Return the fixed test-only challenge baseline and synthetic failure examples."""

    if not CHALLENGE_REPORT_PATH.exists():
        raise HTTPException(status_code=503, detail="Agent challenge report is not available.")
    return json.loads(CHALLENGE_REPORT_PATH.read_text(encoding="utf-8"))


@algorithm_router.get("/agent-runs")
def algorithm_agent_runs(
    limit: int = Query(default=20, ge=1, le=50),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List sanitized decision summaries for only the authenticated user."""

    runs = list(
        db.scalars(
            select(models.AgentRun)
            .where(models.AgentRun.user_id == current_user.id)
            .order_by(models.AgentRun.started_at.desc())
            .limit(limit)
        )
    )
    if not runs:
        return []
    calls = list(
        db.scalars(
            select(models.ToolCall).where(models.ToolCall.agent_run_id.in_([r.id for r in runs]))
        )
    )
    calls_by_run: dict[str, list[models.ToolCall]] = {}
    for call in calls:
        calls_by_run.setdefault(str(call.agent_run_id), []).append(call)
    return [
        analyze_agent_run(run, calls_by_run.get(str(run.id), []), include_timeline=False)
        for run in runs
    ]


@algorithm_router.get("/agent-runs/{run_id}")
def algorithm_agent_run(
    run_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return a sanitized decision replay without raw inputs, outputs, or log paths."""

    try:
        run_uuid = uuid.UUID(run_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Agent run was not found.") from None
    run = db.get(models.AgentRun, run_uuid)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run was not found.")
    if run.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Agent run belongs to another user.")
    calls = list(db.scalars(select(models.ToolCall).where(models.ToolCall.agent_run_id == run.id)))
    return analyze_agent_run(run, calls)
