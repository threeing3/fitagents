import asyncio
import json

import pytest

from fast_api.app.services.intent_cascade import (
    ExampleVectorIndex,
    IntentCascadePolicy,
    RouteExample,
)
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.intent_decision_engine import IntentDecisionEngine
from fast_api.app.services.llm_intent_classifier import LLMIntentClassifier
from tests.test_intent_decision_engine import FakeModelProvider


class Encoder:
    model_id = "controlled-vectors-not-a-real-model"

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(texts)
        return [[3.0, 0.0] if "饭" in text or "饮食" in text else [0.0, 2.0] for text in texts]


def make_index(encoder):
    return ExampleVectorIndex(
        [
            RouteExample("查询锻炼历史", "memory_query", "history"),
            RouteExample("推荐饮食", "nutrition_advice", "meal"),
        ],
        encoder=encoder,
    )


def test_semantic_vectors_normalized_cached_and_not_lexical():
    encoder = Encoder()
    index = make_index(encoder)
    assert index.search("晚饭怎么吃")[0]["intent"] == "nutrition_advice"
    assert index.search("晚饭怎么吃")[0]["score"] == pytest.approx(1)
    assert len(encoder.calls) == 3  # examples once, queries twice
    assert index.search("查看跑步历史")[0]["intent"] == "memory_query"


@pytest.mark.parametrize("bad", [[], [0, 0], [float("nan"), 1], [True, 1], ["1", 0]])
def test_invalid_embeddings_are_not_candidates(bad):
    class BadEncoder:
        model_id = "bad-test"

        def encode(self, texts):
            return [bad for _ in texts]

    candidates, status = make_index(BadEncoder()).retrieve("问一个问题")
    assert candidates == []
    assert status == "semantic_encoder_unavailable_or_invalid"


def test_unavailable_model_does_not_block_llm_fallback():
    class MissingEncoder:
        model_id = "missing-test"

        def encode(self, texts):
            raise FileNotFoundError("private-local-path")

    policy = IntentCascadePolicy(make_index(MissingEncoder()))
    provider = FakeModelProvider("not-json")
    result = asyncio.run(IntentDecisionEngine(provider, cascade_policy=policy).decide("晚饭怎么吃"))
    assert provider.model.calls == 1
    trace = result.decision.provenance["cascade"]
    assert trace["candidates"] == []
    assert "unavailable" in trace["reason"]
    assert "private-local-path" not in str(trace)


def test_rules_skip_encoder_and_similarity_never_authorizes_writes():
    encoder = Encoder()
    policy = IntentCascadePolicy(make_index(encoder))
    assert policy.assess("你好", IntentRouter().analyze("你好")).outcome == "accept"
    assert encoder.calls == []
    assert policy.assess("晚饭怎么吃", IntentRouter().analyze("晚饭怎么吃")).outcome == "escalate"


def test_empty_and_long_inputs_do_not_encode():
    encoder = Encoder()
    index = make_index(encoder)
    assert index.search("") == []
    assert index.search("a" * 2001) == []
    assert index.search("晚饭", limit=0) == []
    assert ExampleVectorIndex([], encoder=encoder).search("晚饭") == []
    assert encoder.calls == []


def test_query_dimension_mismatch_escalates():
    class WrongDimension:
        model_id = "wrong-test"

        def encode(self, texts):
            return [[1, 0] for _ in texts] if len(texts) == 2 else [[1, 0, 0]]

    assert make_index(WrongDimension()).retrieve("问题")[1].endswith("invalid")


def test_candidates_reach_model_as_hints_not_complete_intents():
    message = "晚饭怎么吃"
    candidates = make_index(Encoder()).search(message)
    classifier = LLMIntentClassifier(FakeModelProvider("not-used"))
    payload = json.loads(
        classifier._user_prompt(message, IntentRouter().analyze(message), None, candidates)
    )
    assert payload["semantic_retrieval_candidates"] == candidates
    assert "not probabilities" in payload["instruction"]
    assert "ALL requests" in payload["instruction"]
