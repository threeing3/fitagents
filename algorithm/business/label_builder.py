"""Business outcome label construction with explicit missingness."""

from __future__ import annotations

from typing import Any


def build_outcome_label(example: dict[str, Any]) -> dict[str, Any]:
    outcome = example.get("outcome") or {}
    accepted = outcome.get("accepted_by_user")
    implementation = outcome.get("implementation_status")
    adherence = outcome.get("adherence_7d")
    return {
        "accepted": None if accepted is None else int(bool(accepted)),
        "implemented": None if implementation is None else int(implementation in {"implemented", "partially_implemented"}),
        "adherence_7d": None if adherence is None else max(0.0, min(1.0, float(adherence))),
        "negative_feedback": None if outcome.get("negative_feedback") is None else int(bool(outcome["negative_feedback"])),
        "safety_status": outcome.get("safety_status"),
        "outcome_status": outcome.get("outcome_status"),
        "label_confidence": float(outcome.get("label_confidence") or 0.0),
        "label_source": outcome.get("label_source") or "unknown",
    }
