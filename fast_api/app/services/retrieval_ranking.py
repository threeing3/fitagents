"""Two-route rank fusion shared by retrieval and fixture evaluation."""

from collections.abc import Hashable, Mapping, Sequence
from typing import TypeVar

Key = TypeVar("Key", bound=Hashable)


def reciprocal_rank_score(item_id: Key, rankings: Sequence[Mapping[Key, int]]) -> float:
    """Normalize equal-weight RRF by the best score of available routes.

    Ranks are zero-based. An unavailable route does not halve fallback scores.
    """
    available = [ranks for ranks in rankings if ranks]
    if not available:
        return 0.0
    return sum(61.0 / (61.0 + ranks[item_id]) for ranks in available if item_id in ranks) / len(
        available
    )


def adjust_memory_score(
    relevance: float, temporal_score: float, applicability_adjustment: float
) -> float:
    """Keep time and applicability bounded; neither creates query relevance."""
    if relevance <= 0:
        return 0.0
    freshness = max(0.0, min(1.0, temporal_score))
    applicability = max(-0.1, min(0.1, applicability_adjustment))
    return max(0.0, relevance * (0.9 + 0.1 * freshness) + applicability)
