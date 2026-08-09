import json
from pathlib import Path

from algorithm.app_algorithms.memory_retrieval_eval import evaluate_retrieval_records


def test_retrieval_report_marks_vector_availability_and_excludes_pseudo_vectors():
    path = Path(__file__).resolve().parents[2] / "tests/evals/retrieval_eval_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    report = evaluate_retrieval_records(cases, k=3)
    assert report["strategies"]["bm25"]["available"] is True
    assert len(cases) >= 80
    assert report["strategies"]["vector"]["available"] is False
    assert report["vector_status"] == "vector unavailable"
    assert report["embedding_policy"] == "explicit_vector_only_no_sha256_pseudo_vector"
    assert (
        report["strategies"]["hybrid"]["recall_at_k"] >= report["strategies"]["bm25"]["recall_at_k"]
    )


def test_retrieval_without_vectors_is_explicitly_incomplete():
    report = evaluate_retrieval_records(
        [{"query": "睡眠", "memories": [{"id": "m1", "text": "睡眠"}], "expected": ["m1"]}],
        k=1,
    )
    assert report["strategies"]["vector"]["available"] is False
    assert "SHA-256" in report["strategies"]["vector"]["reason"]


def test_provider_derived_vector_scores_require_provenance():
    report = evaluate_retrieval_records(
        [
            {
                "query": "sleep",
                "expected": ["m1"],
                "memories": [
                    {
                        "id": "m1",
                        "text": "recovery",
                        "vector_score": 0.9,
                        "vector_score_source": "embedding_service",
                    },
                    {
                        "id": "m2",
                        "text": "food",
                        "vector_score": 0.1,
                        "vector_score_source": "embedding_service",
                    },
                ],
            }
        ],
        k=1,
    )
    assert report["strategies"]["vector"]["available"] is True
    assert report["strategies"]["vector"]["recall_at_k"] == 1.0
