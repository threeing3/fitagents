"""Leakage-aware numeric features from canonical examples.

Outcome fields are deliberately excluded by default.  They are labels or
post-response observations, so using them as features would make offline
acceptance metrics look better without improving a future recommendation.
"""

from __future__ import annotations

from typing import Any


def build_features(example: dict[str, Any], include_outcome_features: bool = False) -> dict[str, float]:
    context = example.get("retrieved_context") or {}
    trace = example.get("tool_trace") or []
    quality = example.get("quality_labels") or {}
    trace_items = trace if isinstance(trace, list) else []
    successful_tools = sum(
        1 for item in trace_items if isinstance(item, dict) and str(item.get("status") or "").lower() in {"success", "ok"}
    )
    features = {
        "message_chars": float(len(str(example.get("user_message") or ""))),
        "context_items": float(len(context) if isinstance(context, dict) else 0),
        "tool_count": float(len(trace_items)),
        "tool_success_rate": float(successful_tools / len(trace_items)) if trace_items else 0.0,
        "risk_present": float(str(example.get("risk_label") or "low").lower() not in {"", "low", "none"}),
        "quality_score": float(quality.get("overall_score") or 0.0),
        "has_guardrail_flags": float(bool((example.get("guardrail_result") or {}).get("flags"))),
    }
    if include_outcome_features:
        # This branch is for teaching leakage detection only; never use it in
        # the acceptance model's production feature set.
        outcome = example.get("outcome") or {}
        features["negative_feedback_leakage_probe"] = float(bool(outcome.get("negative_feedback")))
    return features
