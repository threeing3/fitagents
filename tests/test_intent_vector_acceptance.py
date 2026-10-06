import json

import pytest

from algorithm.inference.intent_catalog import AgentIntentCatalog
from fast_api.app.services.intent_cascade import ExampleVectorIndex
from fast_api.app.services.intent_route_examples import EXAMPLE_TEXTS
from scripts.evaluate_intent_vectors import FIXTURE, audit_cases, retrieval_metrics


def test_example_coverage_and_acceptance_split():
    assert set(EXAMPLE_TEXTS) == AgentIntentCatalog.VALID_INTENTS
    assert all(len(texts) == 4 for texts in EXAMPLE_TEXTS.values())
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    audit = audit_cases(payload, ExampleVectorIndex())
    assert audit["case_count"] == 62
    assert audit["example_count"] == 64
    singles = [case for case in payload["cases"] if case["group"] == "single"]
    assert {case["expected"][0] for case in singles} == AgentIntentCatalog.VALID_INTENTS


def test_audit_rejects_test_query_as_example():
    index = ExampleVectorIndex()
    payload = {
        "cases": [
            {
                "id": "bad",
                "group": "single",
                "text": index.examples[0].text,
                "expected": [index.examples[0].intent],
            }
        ]
    }
    with pytest.raises(ValueError, match="overlap"):
        audit_cases(payload, index)


def test_one_intent_cannot_fill_all_candidate_slots():
    ranked = [
        {"example_id": "a", "intent": "memory_query", "score": 0.99},
        {"example_id": "b", "intent": "memory_query", "score": 0.98},
        {"example_id": "c", "intent": "training_plan", "score": 0.95},
        {"example_id": "d", "intent": "nutrition_advice", "score": 0.90},
    ]
    selected = ExampleVectorIndex.distinct_intents(ranked)
    assert [item["example_id"] for item in selected] == ["a", "c", "d"]


def test_multitask_metric_requires_every_task_not_just_primary():
    candidates = [{"intent": "training_log"}, {"intent": "recovery_check"}]
    result = retrieval_metrics(candidates, ["training_log", "nutrition_advice"])
    assert result["top1_correct"] is True
    assert result["complete_top3"] is False
    assert result["task_recall_top3"] == 0.5
    assert retrieval_metrics(candidates, [])["task_recall_top3"] is None
