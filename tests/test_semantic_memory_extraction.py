import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.intent_decision_engine import IntentDecisionEngine
from fast_api.app.services.llm_intent_classifier import LLMIntentClassifier
from fast_api.app.services.runtime_router import RuntimeRoute
from fast_api.app.services.semantic_memory_extraction import (
    bounded_background,
    extract_semantic_memories,
    validate_memory_payload,
)


def candidate(**overrides):
    return {
        "content": "用户偏好骑车",
        "memory_type": "stable_preference",
        "evidence_quote": "我喜欢骑车",
        "subject": "user",
        "assertion": "fact",
        "polarity": "positive",
        "time_scope": "stable",
        "confidence": 0.86,
        **overrides,
    }


class Model:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if isinstance(self.payload, Exception):
            raise self.payload
        return SimpleNamespace(
            content=json.dumps(self.payload), usage_metadata={"input_tokens": 10}
        )


class Provider:
    def __init__(self, payload):
        self.model = Model(payload)
        self.settings = SimpleNamespace(llm_provider="deepseek", chat_model="fake")

    def chat_model(self, temperature=0):
        return self.model

    def has_live_model(self):
        return True


def payload(**overrides):
    return {
        "primary_intent": "general_chat",
        "secondary_intents": [],
        "risk_level": "low",
        "confidence": 0.9,
        "entities": {},
        "missing_slots": [],
        "needs_clarification": False,
        "memory_candidates": [candidate()],
        **overrides,
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"subject": "friend"},
        {"assertion": "hypothetical"},
        {"assertion": "question"},
        {"evidence_quote": "不存在的来源"},
        {"memory_type": "delete_all"},
        {"confidence": float("nan")},
        {"confidence": True},
        {"content": ""},
    ],
)
def test_invalid_candidates_are_rejected(patch):
    result = validate_memory_payload({"memory_candidates": [candidate(**patch)]}, "我喜欢骑车")
    assert result["candidates"] == []
    assert result["rejected"] == 1


def test_temporary_preference_is_not_saved_as_stable():
    result = validate_memory_payload(
        {"memory_candidates": [candidate(time_scope="recent")]}, "我喜欢骑车"
    )
    assert result["candidates"][0]["memory_type"] == "recent_state"


def test_denial_and_empty_results_are_authoritative():
    assert not validate_memory_payload(payload(), "我喜欢骑车，但不要记住")["candidates"]
    assert validate_memory_payload({"memory_candidates": []}, "我喜欢骑车")["status"] == "validated"


def test_one_call_shares_intent_and_memories_with_bounded_background():
    provider = Provider(payload())
    result = asyncio.run(
        IntentDecisionEngine(provider).decide(
            "我喜欢骑车",
            recent_conversation=[{"role": "user", "content": "之前喜欢跑步"}],
        )
    )
    assert len(provider.model.calls) == 1
    assert result.memory_extraction["candidates"][0]["content"] == "用户偏好骑车"
    assert "之前喜欢跑步" in provider.model.calls[0][1].content
    assert "memory_candidates" not in json.dumps(result.decision.to_dict())


def test_invalid_memory_does_not_destroy_valid_intent():
    provider = Provider(payload(primary_intent="nutrition_advice", memory_candidates="bad"))
    decision, trace = asyncio.run(
        LLMIntentClassifier(provider).refine_with_trace(
            "我喜欢骑车",
            IntentRouter().analyze("我喜欢骑车"),
            force_refine=True,
            extract_memory=True,
            authoritative_tasks=True,
        )
    )
    assert decision.primary_intent == "nutrition_advice"
    assert trace["succeeded"]
    assert trace["memory_extraction"]["status"] == "invalid"


def test_invalid_intent_does_not_destroy_grounded_memory():
    provider = Provider(payload(primary_intent="not-an-intent"))
    _, trace = asyncio.run(
        LLMIntentClassifier(provider).refine_with_trace(
            "我喜欢骑车",
            IntentRouter().analyze("我喜欢骑车"),
            force_refine=True,
            extract_memory=True,
            authoritative_tasks=True,
        )
    )
    assert not trace["succeeded"]
    assert trace["memory_extraction"]["candidates"]


def test_memory_failure_is_closed_and_independent():
    result = asyncio.run(extract_semantic_memories(Provider(RuntimeError("failed")), "我喜欢骑车"))
    assert result["status"] == "failed"
    assert result["candidates"] == []


def test_profile_extraction_reuses_shared_result_and_never_keyword_falls_back():
    provider = Provider(payload())
    service = CoachAgentService(None, provider)
    extraction = asyncio.run(
        service.profile_extractor_agent(
            SimpleNamespace(),
            "我喜欢骑车",
            semantic_memory={"status": "failed", "candidates": []},
        )
    )
    assert provider.model.calls == []
    assert service._memory_candidates_from_message("我喜欢骑车", extraction) == []


def test_private_route_excludes_memory_and_history_is_bounded():
    route = RuntimeRoute("code_driven", "test", memory_extraction=payload())
    assert "memory_extraction" not in route.to_dict()
    history = bounded_background([{"role": "user", "content": "a" * 1000}] * 20)
    assert len(history) == 6 and len(history[0]["content"]) == 800


def test_simple_confirmation_skips_extra_memory_model():
    provider = Provider(payload())
    service = CoachAgentService(None, provider)
    extraction = asyncio.run(service.profile_extractor_agent(SimpleNamespace(), "谢谢"))
    assert provider.model.calls == []
    assert extraction["semantic_memory"]["status"] == "not_required"


def test_semantic_candidates_reach_durable_memory_with_source_metadata():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from fast_api.app.core.config import Settings
    from fast_api.app.db import models
    from fast_api.app.db.database import Base
    from fast_api.app.services.model_provider import ModelProvider

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    provider = ModelProvider(
        Settings(llm_provider="offline", embedding_provider="offline", use_pgvector=False)
    )
    with Session(engine) as db:
        service = CoachAgentService(db, provider)
        user = models.User(
            id=uuid.uuid4(), email="semantic-test@example.invalid", password_hash="synthetic"
        )
        db.add(user)
        db.flush()
        extraction = {
            "corrections": [],
            "semantic_memory": validate_memory_payload(payload(), "我喜欢骑车"),
        }
        written = service.write_memories_from_message(user.id, "我喜欢骑车", extraction)
        db.commit()
        memories = list(
            db.scalars(
                select(models.LongTermMemory).where(models.LongTermMemory.user_id == user.id)
            )
        )
        assert len(written) == len(memories) == 1
        assert memories[0].content == "用户偏好骑车"
        assert memories[0].memory_metadata["evidence_quote"] == "我喜欢骑车"
        assert memories[0].memory_metadata["importance_source"] == "host_type_policy"
    engine.dispose()


def test_negative_medical_fact_does_not_create_risk_note():
    service = CoachAgentService(None, Provider(payload()))
    service._write_risk_note_from_memory(
        uuid.uuid4(),
        SimpleNamespace(
            memory_type="medical_context",
            memory_metadata={"assertion_polarity": "negative"},
        ),
    )


def test_plan_modification_cannot_be_labeled_as_memory():
    result = validate_memory_payload(
        {
            "memory_candidates": [
                candidate(
                    content="用户希望明天骑车",
                    evidence_quote="把明天的跑步换成骑车",
                )
            ]
        },
        "我最近膝盖不舒服，把明天的跑步换成骑车",
    )
    assert result["candidates"] == []


def test_shared_memory_is_reused_across_tool_retries():
    from fast_api.app.db import models

    provider = Provider(payload())
    service = CoachAgentService(None, provider)
    user_id = uuid.uuid4()
    source_id = str(uuid.uuid4())
    profile = models.UserProfile(user_id=user_id)
    registry = service._build_chat_tool_registry(
        user_id,
        uuid.uuid4(),
        profile,
        "我喜欢骑车",
        workout_request_key=source_id,
        memory_extraction=validate_memory_payload(payload(), "我喜欢骑车"),
    )

    async def run():
        first = await registry.execute("profile.extract", {"message_chars": 5})
        second = await registry.execute("profile.extract", {"message_chars": 5})
        verified = await registry.execute("memory.verify", {"extraction": first.output_json})
        return first, second, verified

    first, second, verified = asyncio.run(run())
    assert first.status == second.status == verified.status == "success"
    assert not provider.model.calls
    accepted = verified.output_json["accepted_candidates"]
    assert len(accepted) == 1
    assert accepted[0]["memory_metadata"]["source_message_id"] == source_id


def test_host_rejects_forged_tool_memory_candidates():
    from fast_api.app.db import models

    service = CoachAgentService(None, Provider(payload()))
    user_id = uuid.uuid4()
    profile = models.UserProfile(user_id=user_id)
    registry = service._build_chat_tool_registry(user_id, uuid.uuid4(), profile, "我喜欢骑车")
    # No extraction receipt: a model cannot supply its own validated-memory envelope.
    result = asyncio.run(
        registry.execute(
            "memory.verify",
            {
                "extraction": {
                    "semantic_memory": validate_memory_payload(payload(), "我喜欢骑车"),
                }
            },
        )
    )
    assert result.status == "success", result
    assert result.output_json["accepted_candidates"] == []
