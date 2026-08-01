"""Leakage-aware numeric features from canonical examples."""

from __future__ import annotations

from typing import Any


def build_features(example: dict[str, Any]) -> dict[str, float]:
    context = example.get("retrieved_context") or {}
    trace = example.get("tool_trace") or []
    quality = example.get("quality_labels") or {}
    outcome = example.get("outcome") or {}
    return {
        "message_chars": float(len(str(example.get("user_message") or ""))),
        "context_items": float(len(context) if isinstance(context, dict) else 0),
        "tool_count": float(len(trace) if isinstance(trace, list) else 0),
        "risk_present": float(str(example.get("risk_label") or "low").lower() not in {"", "low", "none"}),
        "quality_score": float(quality.get("overall_score") or 0.0),
        "negative_feedback": float(bool(outcome.get("negative_feedback"))),
        "has_guardrail_flags": float(bool((example.get("guardrail_result") or {}).get("flags"))),
    }
