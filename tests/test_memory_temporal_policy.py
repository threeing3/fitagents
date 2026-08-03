import uuid
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.memory_temporal_policy import MemoryTemporalPolicy


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def memory_stub(
    *,
    fact_kind: str,
    category: str,
    occurred_end: datetime | None = None,
    occurred_start: datetime | None = None,
    mentioned_at: datetime | None = None,
    created_at: datetime | None = None,
    metadata: dict | None = None,
    entities: list[dict] | None = None,
):
    return models.LongTermMemory(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        memory_type=fact_kind,
        memory_network="experience" if "strategy" in fact_kind else "world",
        fact_kind=fact_kind,
        category=category,
        content="temporal test memory",
        summary="temporal test memory",
        importance=0.5,
        confidence=0.8,
        source="test",
        occurred_end=occurred_end,
        occurred_start=occurred_start,
        mentioned_at=mentioned_at,
        created_at=created_at,
        memory_metadata=metadata or {},
        entities=entities or [],
    )


def test_temporal_policy_uses_event_time_priority_and_half_life():
    as_of = datetime(2026, 6, 25, 12, 0, 0)
    memory = memory_stub(
        fact_kind="recovery_event",
        category="recovery",
        occurred_end=as_of - timedelta(days=5),
        occurred_start=as_of - timedelta(days=4),
        mentioned_at=as_of,
        created_at=as_of,
    )

    result = MemoryTemporalPolicy().score(memory, as_of=as_of)

    assert result.reference_time == as_of - timedelta(days=5)
    assert result.half_life_days == 5
    assert result.score == 0.5


def test_temporal_policy_clamps_future_time_and_preserves_health_fact():
    as_of = datetime(2026, 6, 25, 12, 0, 0)
    future = memory_stub(
        fact_kind="recovery_event",
        category="recovery",
        occurred_end=as_of + timedelta(days=3),
    )
    health = memory_stub(
        fact_kind="health_fact",
        category="risk",
        occurred_end=as_of - timedelta(days=3000),
    )

    assert MemoryTemporalPolicy().score(future, as_of=as_of).age_days == 0
    assert MemoryTemporalPolicy().score(future, as_of=as_of).score == 1
    assert MemoryTemporalPolicy().score(health, as_of=as_of).score == 1
    assert MemoryTemporalPolicy().score(health, as_of=as_of).half_life_days is None


def test_temporal_policy_uses_neutral_score_when_time_is_missing():
    memory = memory_stub(fact_kind="unknown", category="other")

    result = MemoryTemporalPolicy().score(memory, as_of=datetime(2026, 6, 25))

    assert result.score == 0.5
    assert result.reference_time is None
    assert result.policy.endswith("missing_time")


def test_strategy_applicability_rewards_similar_context_and_penalizes_goal_conflict():
    memory = memory_stub(
        fact_kind="strategy_experience",
        category="training",
        metadata={
            "goal": "fat_loss",
            "training_phase": "base",
            "baseline_state": {"fatigue": 8, "sleep_hours": 6},
        },
        entities=[{"type": "symptom", "canonical": "knee"}],
    )
    policy = MemoryTemporalPolicy()

    similar = policy.applicability(
        memory,
        {
            "goal": "fat_loss",
            "training_phase": "base",
            "baseline_state": {"fatigue": 7.5, "sleep_hours": 6.5},
            "entities": [{"type": "symptom", "canonical": "knee"}],
        },
    )
    conflicting = policy.applicability(
        memory,
        {
            "goal": "muscle_gain",
            "training_phase": "peak",
            "baseline_state": {"fatigue": 2, "sleep_hours": 9},
            "entities": [],
        },
    )

    assert similar.adjustment > 0
    assert conflicting.adjustment < 0
    assert similar.score > conflicting.score


def test_legacy_strategy_without_metadata_is_neutral():
    memory = memory_stub(fact_kind="strategy_experience", category="training")

    result = MemoryTemporalPolicy().applicability(memory, {"goal": "strength"})

    assert result.score == 0.5
    assert result.adjustment == 0


def test_search_prefers_recent_event_time_and_exposes_score_components(monkeypatch):
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    as_of = datetime(2026, 6, 25, 12, 0, 0)
    old = manager.retain_memory(
        user_id,
        "same recovery marker",
        "world",
        "recovery_event",
        category="recovery",
        occurred_end=as_of - timedelta(days=30),
    )
    recent = manager.retain_memory(
        user_id,
        "same recovery marker",
        "world",
        "recovery_event",
        category="recovery",
        occurred_end=as_of - timedelta(days=1),
    )
    monkeypatch.setattr(manager, "_semantic_candidates", lambda query, filters, top_k: [])

    results = manager.search_memories(user_id, "same recovery marker", top_k=2, as_of=as_of)

    assert results[0].id == recent.id
    assert results[0].retrieval_debug["temporal_score"] > old.retrieval_debug["temporal_score"]
    assert "score_components" in results[0].retrieval_debug


def test_search_hard_filters_future_validity_expired_and_superseded(monkeypatch):
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    as_of = datetime(2026, 6, 25, 12, 0, 0)
    active = manager.retain_memory(user_id, "filter marker active", "world", "preference")
    future = manager.retain_memory(user_id, "filter marker future", "world", "preference")
    expired = manager.retain_memory(user_id, "filter marker expired", "world", "preference")
    superseded = manager.retain_memory(user_id, "filter marker superseded", "world", "preference")
    future.valid_from = as_of + timedelta(days=1)
    expired.valid_until = as_of - timedelta(seconds=1)
    superseded.status = "superseded"
    db.flush()
    monkeypatch.setattr(manager, "_semantic_candidates", lambda query, filters, top_k: [])

    results = manager.search_memories(user_id, "filter marker", top_k=10, as_of=as_of)

    assert [item.id for item in results] == [active.id]


def test_safety_failed_strategy_has_long_floor_but_needs_query_relevance(monkeypatch):
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    as_of = datetime(2026, 6, 25, 12, 0, 0)
    safety_failure = manager.retain_memory(
        user_id,
        "sharp knee pain after aggressive squat progression",
        "experience",
        "failed_strategy",
        category="risk",
        occurred_end=as_of - timedelta(days=730),
        metadata={"safety_relevant": True},
    )
    manager.retain_memory(
        user_id,
        "user prefers simple high protein breakfast",
        "world",
        "nutrition_event",
        category="nutrition",
        occurred_end=as_of - timedelta(days=1),
    )
    monkeypatch.setattr(manager, "_semantic_candidates", lambda query, filters, top_k: [])

    unrelated = manager.search_memories(user_id, "protein breakfast", top_k=5, as_of=as_of)
    related = manager.search_memories(user_id, "sharp knee pain squat", top_k=5, as_of=as_of)

    assert safety_failure.id not in [item.id for item in unrelated]
    matched = next(item for item in related if item.id == safety_failure.id)
    assert matched.retrieval_debug["temporal_floor"] == 0.60
