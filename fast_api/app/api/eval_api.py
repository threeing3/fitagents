"""Evaluation API — run and retrieve LLM output quality evaluations."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.services.eval_service import EvalService
from fast_api.app.services.model_provider import ModelProvider

eval_router = APIRouter(prefix="/v1/eval", tags=["evaluation"])


def get_eval_service(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> EvalService:
    model_provider = ModelProvider()
    return EvalService(db, model_provider=model_provider)


# ---- Evaluate a single response ----

@eval_router.post("/evaluate")
async def evaluate_response(
    request_body: dict[str, Any],
    request: Any,  # Request for limiter
    service: EvalService = Depends(get_eval_service),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    """Evaluate a single coach response for quality.

    Body:
    {
        "user_message": "string",
        "coach_response": "string",
        "context": "string (optional, retrieved knowledge)",
        "ground_truth": "string (optional, reference answer)"
    }

    Returns per-dimension scores (1-5) and overall pass/fail.
    """
    user_message = request_body.get("user_message", "")
    coach_response = request_body.get("coach_response", "")

    if not user_message or not coach_response:
        raise HTTPException(
            status_code=400,
            detail="Both 'user_message' and 'coach_response' are required.",
        )

    result = await service.evaluate(
        user_message=user_message,
        coach_response=coach_response,
        context=request_body.get("context"),
        ground_truth=request_body.get("ground_truth"),
    )
    return _result_to_dict(result, request_body)


# ---- Run an evaluation suite ----

@eval_router.post("/suite")
async def run_eval_suite(
    request_body: dict[str, Any],
    request: Any,
    service: EvalService = Depends(get_eval_service),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    """Run a batch evaluation suite.

    Body:
    {
        "suite_name": "string",
        "cases": [{"input": "string", "expected": {...}}],
        "persist": true,
        "model_used": "string (optional)",
        "prompt_version": "string (optional)"
    }
    """
    suite_name = request_body.get("suite_name", f"manual-{uuid.uuid4().hex[:8]}")
    cases = request_body.get("cases")
    if not cases:
        raise HTTPException(status_code=400, detail="'cases' list is required.")

    result = await service.run_suite(
        suite_name=suite_name,
        cases=cases,
        persist=request_body.get("persist", True),
        model_used=request_body.get("model_used"),
        prompt_version=request_body.get("prompt_version"),
    )
    return result


# ---- Run the built-in eval suite (from eval_cases.json) ----

@eval_router.post("/suite/builtin")
async def run_builtin_suite(
    request: Any,
    service: EvalService = Depends(get_eval_service),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    """Run the built-in eval suite from eval_cases.json.
    Evaluates the knowledge retrieval pipeline (not LLM responses).
    """
    result = await service.run_suite(
        suite_name="builtin-knowledge",
        cases=None,  # Loads from JSON file
        persist=True,
        model_used="rule-based",
        prompt_version="builtin",
    )
    return result


# ---- List evaluation runs ----

@eval_router.get("/runs")
def list_eval_runs(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List recent evaluation runs."""
    runs = db.scalars(
        select(models.EvalRun)
        .order_by(desc(models.EvalRun.created_at))
        .limit(limit)
    ).all()
    return [
        {
            "id": str(run.id),
            "suite_name": run.suite_name,
            "model_used": run.model_used,
            "prompt_version": run.prompt_version,
            "total_cases": run.total_cases,
            "passed_count": run.passed_count,
            "average_score": run.average_score,
            "dimension_averages": run.dimension_averages,
            "started_at": str(run.started_at) if run.started_at else None,
            "completed_at": str(run.completed_at) if run.completed_at else None,
            "created_at": str(run.created_at),
        }
        for run in runs
    ]


# ---- Get run details ----

@eval_router.get("/runs/{run_id}")
def get_eval_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    """Get evaluation run details with individual results."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run ID format.")

    run = db.get(models.EvalRun, run_uuid)
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found.")

    results = db.scalars(
        select(models.EvalResult)
        .where(models.EvalResult.eval_run_id == run_uuid)
    ).all()

    return {
        "id": str(run.id),
        "suite_name": run.suite_name,
        "model_used": run.model_used,
        "prompt_version": run.prompt_version,
        "total_cases": run.total_cases,
        "passed_count": run.passed_count,
        "average_score": run.average_score,
        "dimension_averages": run.dimension_averages,
        "started_at": str(run.started_at) if run.started_at else None,
        "completed_at": str(run.completed_at) if run.completed_at else None,
        "results": [
            {
                "id": str(r.id),
                "score": r.score,
                "passed": r.passed,
                "dimension_scores": r.dimension_scores_json,
                "rule_checks": r.rule_checks_json,
                "details": r.details,
            }
            for r in results
        ],
    }


# ---- Helpers ----

def _result_to_dict(
    result: Any,
    request_body: dict[str, Any],
) -> dict[str, Any]:
    return {
        "overall_score": result.overall_score,
        "passed": result.passed,
        "dimension_scores": [
            {
                "dimension": ds.dimension.value,
                "score": ds.score,
                "explanation": ds.explanation,
                "passed": ds.passed,
            }
            for ds in result.dimension_scores
        ],
        "rule_checks": result.rule_checks,
        "rule_details": result.rule_details,
        "summary": result.summary,
    }
