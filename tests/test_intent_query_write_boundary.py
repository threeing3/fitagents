"""Isolated database checks for query-only messages at the real memory write boundary."""

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.model_provider import ModelProvider


@pytest.mark.parametrize(
    "message",
    [
        "请查一下我上次卧推用了多少公斤；这次不要新增训练记录。",
        "告诉我上次深蹲做了几组，我没有要记录今天的训练。",
    ],
)
def test_query_only_message_does_not_create_training_memory(message: str) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    provider = ModelProvider(
        Settings(
            _env_file=None,
            llm_provider="offline",
            embedding_provider="offline",
            use_pgvector=False,
            jwt_secret_key="synthetic-query-boundary-only",
        )
    )
    with Session(engine) as db:
        user = models.User(
            id=uuid.uuid4(), email="query-boundary@example.test", password_hash="not-a-login"
        )
        db.add(user)
        db.flush()
        profile = models.UserProfile(user_id=user.id)
        db.add(profile)
        db.commit()
        service = CoachAgentService(db, provider)
        extraction = service._rule_profile_extraction(message)
        verification = service._verify_memory_tool(user.id, message, extraction, profile)
        written = service.write_memories_from_message(user.id, message, extraction, verification)
        db.commit()

        assert written == []
        assert (
            db.scalars(
                select(models.LongTermMemory).where(models.LongTermMemory.user_id == user.id)
            ).all()
            == []
        )
    engine.dispose()


def test_affirmative_training_fact_still_creates_memory_candidate() -> None:
    service = CoachAgentService.__new__(CoachAgentService)

    candidates = service._memory_candidates_from_message(
        "昨晚卧推60公斤做了三组，请新增一条训练记录。", extraction={}
    )

    assert any(item["memory_type"] == "training_performance" for item in candidates)


@pytest.mark.parametrize(
    "message",
    [
        "我今天卧推做了三组，但不要记录。",
        "今天深蹲做了五组，请记下来；再告诉我上次做了几组。",
    ],
)
def test_denied_or_mixed_query_does_not_make_raw_training_memory(message: str) -> None:
    service = CoachAgentService.__new__(CoachAgentService)

    candidates = service._memory_candidates_from_message(message, extraction={})

    assert not any(item["memory_type"] == "training_performance" for item in candidates)
