"""Deterministic safety-first response candidate ranking."""

from __future__ import annotations

from typing import Any

from fast_api.app.core.eval_metrics import check_safety_rules


def score_candidate(response: str, query: str = "", context_terms: list[str] | None = None) -> dict[str, Any]:
    safety = check_safety_rules(response)
    checks = safety.get("checks", {})
    safe = bool(checks.get("no_harmful_content", False))
    grounded_hits = sum(1 for term in (context_terms or []) if term and term.lower() in response.lower())
    actionable = sum(1 for term in ("建议", "可以", "步骤", "先", "start", "recommend") if term.lower() in response.lower())
    score = (4.0 if safe else -10.0) + min(2.0, grounded_hits * 0.25) + min(1.0, actionable * 0.2)
    return {"score": round(score, 4), "safe": safe, "grounded_hits": grounded_hits, "actionable_hits": actionable, "checks": checks}


def select_best_candidate(candidates: list[str], query: str = "", context_terms: list[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    scored = [{"response": response, **score_candidate(response, query, context_terms)} for response in candidates]
    scored.sort(key=lambda item: (item["safe"], item["score"]), reverse=True)
    return (scored[0]["response"] if scored else ""), scored
