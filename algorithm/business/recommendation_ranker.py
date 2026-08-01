"""Safety-constrained business candidate ranking."""

from __future__ import annotations

from typing import Any


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(candidate: dict[str, Any]) -> tuple:
        safety = 1 if candidate.get("safe", False) else 0
        predicted = float(candidate.get("predicted_acceptance", 0.0) or 0.0)
        quality = float(candidate.get("quality_score", 0.0) or 0.0)
        actionable = float(candidate.get("actionability", 0.0) or 0.0)
        return safety, predicted, quality, actionable

    return sorted((dict(item) for item in candidates), key=key, reverse=True)
