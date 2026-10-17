"""Dependency-light retrieval baselines and metrics.

The vector strategy accepts only an explicit ``vector_score`` supplied by a
real embedding service or an offline fixture.  It intentionally never creates
SHA-256 pseudo-vectors, so a missing embedding dependency is visible in the
report rather than turning into a false semantic-retrieval claim.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Iterable

from fast_api.app.services.bm25 import rank_by_bm25
from fast_api.app.services.retrieval_ranking import adjust_memory_score, reciprocal_rank_score


def recall_at_k(retrieved: Iterable[str], expected: Iterable[str], k: int = 5) -> float:
    expected_set = {str(item).lower() for item in expected if item}
    if not expected_set:
        return 1.0
    returned = {str(item).lower() for item in list(retrieved)[:k]}
    return len(returned & expected_set) / len(expected_set)


def mean_recall_at_k(records: list[dict], k: int = 5) -> float:
    scores = [
        recall_at_k(
            record.get("retrieved") or record.get("relevant_memories") or [],
            record.get("expected") or record.get("expected_recalled_terms") or [],
            k,
        )
        for record in records
    ]
    return sum(scores) / len(scores) if scores else 0.0


def _memory_id(record: dict[str, Any]) -> str:
    return str(record.get("id") or record.get("memory_id") or record.get("key") or "")


def _memory_text(record: dict[str, Any]) -> str:
    return " ".join(
        str(record.get(field) or "")
        for field in ("text", "content", "summary", "entity", "memory_type")
    ).strip()


def bm25_rank(records: list[dict[str, Any]], query: str) -> list[str]:
    """Return IDs sorted by the production BM25 implementation."""

    matches = rank_by_bm25(records, query, _memory_text)
    indexed = [(index, match) for index, match in enumerate(matches) if match.score > 0]
    indexed.sort(key=lambda item: (-item[1].score, item[0]))
    return [_memory_id(match.item) for _, match in indexed]


def vector_rank(records: list[dict[str, Any]]) -> list[str] | None:
    """Rank only provider-derived semantic scores with explicit provenance."""

    if not records or any(
        record.get("vector_score") is None
        or record.get("vector_score_source")
        not in {"embedding_service", "precomputed_embedding_service"}
        for record in records
    ):
        return None
    indexed = list(enumerate(records))
    indexed.sort(key=lambda item: (-float(item[1].get("vector_score") or 0.0), item[0]))
    return [_memory_id(record) for _, record in indexed]


def hybrid_rank(records: list[dict[str, Any]], query: str) -> tuple[list[str], dict[str, Any]]:
    """Use runtime fusion/scoring on explicit fixture signals, not DB recall."""

    matches = rank_by_bm25(records, query, _memory_text)
    lexical = sorted(
        (match for match in matches if match.score > 0), key=lambda m: m.score, reverse=True
    )
    keyword_ranks = {_memory_id(match.item): index for index, match in enumerate(lexical)}
    semantic = vector_rank(records)
    has_vector = semantic is not None
    vector_ranks = {memory_id: index for index, memory_id in enumerate(semantic or [])}
    combined: list[tuple[float, int, str]] = []
    for index, record in enumerate(records):
        memory_id = _memory_id(record)
        relevance = reciprocal_rank_score(memory_id, [vector_ranks, keyword_ranks])
        if relevance <= 0:
            continue
        score = adjust_memory_score(
            relevance,
            float(record.get("recency_score", 0.5)),
            float(record.get("applicability_adjustment", 0.0)),
        )
        combined.append((score, index, memory_id))
    combined.sort(key=lambda item: (-item[0], item[1]))
    return [memory_id for _, _, memory_id in combined], {
        "uses_explicit_vector_score": has_vector,
        "vector_fallback": not has_vector,
        "policy": "no_sha256_pseudo_vector",
        "ranker": "two_route_rrf_temporal_applicability",
        "evaluation_scope": "fixture_scoring_not_database_recall",
    }


def _latency_percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 4)


def evaluate_retrieval_records(records: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    """Evaluate BM25/vector/hybrid retrieval with availability and latency."""

    strategy_rows: dict[str, list[dict[str, Any]]] = {"bm25": [], "vector": [], "hybrid": []}
    latency: dict[str, list[float]] = {name: [] for name in strategy_rows}
    vector_available = bool(records) and all(
        isinstance(item.get("memories"), list)
        and item["memories"]
        and vector_rank(item["memories"]) is not None
        for item in records
    )
    for item in records:
        memories = list(item.get("memories") or [])
        expected = item.get("expected") or item.get("expected_recalled_terms") or []
        query = str(item.get("query") or "")
        started = time.perf_counter()
        bm25 = bm25_rank(memories, query)
        latency["bm25"].append((time.perf_counter() - started) * 1000)
        strategy_rows["bm25"].append({"retrieved": bm25, "expected": expected})
        if vector_available:
            started = time.perf_counter()
            vector = vector_rank(memories) or []
            latency["vector"].append((time.perf_counter() - started) * 1000)
            strategy_rows["vector"].append({"retrieved": vector, "expected": expected})
        started = time.perf_counter()
        hybrid, _ = hybrid_rank(memories, query)
        latency["hybrid"].append((time.perf_counter() - started) * 1000)
        strategy_rows["hybrid"].append({"retrieved": hybrid, "expected": expected})
    strategies: dict[str, Any] = {}
    for name in ("bm25", "hybrid"):
        strategies[name] = {
            "available": True,
            "recall_at_k": mean_recall_at_k(strategy_rows[name], k),
            "p50_latency_ms": _latency_percentile(latency[name], 0.50),
            "p95_latency_ms": _latency_percentile(latency[name], 0.95),
        }
    if vector_available:
        strategies["vector"] = {
            "available": True,
            "recall_at_k": mean_recall_at_k(strategy_rows["vector"], k),
            "p50_latency_ms": _latency_percentile(latency["vector"], 0.50),
            "p95_latency_ms": _latency_percentile(latency["vector"], 0.95),
        }
    else:
        strategies["vector"] = {
            "available": False,
            "status": "vector unavailable",
            "reason": (
                "no embedding-service scores with explicit provenance; "
                "SHA-256 pseudo-vectors are excluded"
            ),
        }
    return {
        "records": len(records),
        "k": k,
        "strategies": strategies,
        "vector_status": "available" if vector_available else "vector unavailable",
        "embedding_policy": "explicit_vector_only_no_sha256_pseudo_vector",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate BM25/vector/hybrid retrieval fixtures")
    parser.add_argument("--cases", type=Path, default=Path("tests/evals/retrieval_eval_cases.json"))
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    report = evaluate_retrieval_records(json.loads(args.cases.read_text(encoding="utf-8")), args.k)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
