"""Synthetic runtime regressions for two-route retrieval, without model requests."""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from algorithm.app_algorithms.memory_retrieval_eval import bm25_rank, hybrid_rank
from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.retrieval_ranking import adjust_memory_score, reciprocal_rank_score


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def offline_provider():
    return ModelProvider(
        Settings(_env_file=None, LLM_PROVIDER="offline", EMBEDDING_PROVIDER="offline")
    )


def test_rank_fusion_normalizes_only_available_routes():
    assert reciprocal_rank_score("a", [{"a": 0}, {}]) == 1.0
    assert reciprocal_rank_score("a", [{"a": 0}, {"b": 0}]) == 0.5
    assert reciprocal_rank_score("a", [{"a": 0}, {"a": 0}]) == 1.0
    assert reciprocal_rank_score("missing", [{"a": 0}]) == 0.0
    assert reciprocal_rank_score("a", [{}, {}]) == 0.0
    assert adjust_memory_score(0.0, 1.0, 0.1) == 0.0
    assert adjust_memory_score(1.0, 1.0, 0.0) > adjust_memory_score(1.0, 0.0, 0.0)
    assert adjust_memory_score(1.0, 0.5, 0.1) > adjust_memory_score(1.0, 0.5, -0.1)


def test_recent_unrelated_high_importance_memory_is_not_injected(db):
    manager = MemoryManager(db, offline_provider())
    user_id = uuid.uuid4()
    manager.retain_memory(
        user_id,
        "早餐喜欢燕麦",
        "world",
        "nutrition_event",
        category="nutrition",
        importance_score=1.0,
    )
    assert manager.search_memories(user_id, "zzzz-unmatched-token") == []


def test_explicit_entity_metadata_is_part_of_keyword_recall(db):
    manager = MemoryManager(db, offline_provider())
    user_id = uuid.uuid4()
    memory = manager.retain_memory(
        user_id,
        "opaque factual note",
        "world",
        "health_fact",
        category="risk",
        entities=[{"type": "medication", "name": "XYZ_MARKER", "canonical": "XYZ_MARKER"}],
    )
    results = manager.search_memories(user_id, "", entities=["XYZ_MARKER"])
    assert [item.id for item in results] == [memory.id]
    assert results[0].retrieval_debug["sources"] == ["keyword"]
    assert set(results[0].retrieval_debug["score_components"]) == {
        "rrf",
        "temporal",
        "applicability",
    }


def test_runtime_keeps_applicability_and_temporal_policy(db):
    manager = MemoryManager(db, offline_provider())
    user_id = uuid.uuid4()
    as_of = datetime(2026, 10, 13)
    conflict = manager.retain_memory(
        user_id,
        "strategy marker",
        "experience",
        "strategy_experience",
        category="training",
        metadata={"goal": "fat_loss"},
        occurred_end=as_of - timedelta(days=1),
    )
    suitable = manager.retain_memory(
        user_id,
        "strategy marker",
        "experience",
        "strategy_experience",
        category="training",
        metadata={"goal": "muscle_gain"},
        occurred_end=as_of - timedelta(days=1),
    )
    results = manager.search_memories(
        user_id,
        "strategy marker",
        current_context={"goal": "muscle_gain"},
        as_of=as_of,
    )
    assert results[0].id == suitable.id
    assert suitable.retrieval_debug["applicability_adjustment"] > 0
    assert conflict.retrieval_debug["applicability_adjustment"] < 0
    assert "temporal_policy" in suitable.retrieval_debug


def test_knowledge_keyword_candidates_are_independent_of_vector_and_recency(db, monkeypatch):
    service = FitnessKnowledgeService(db, offline_provider())
    relevant = models.ExplanationKnowledge(
        knowledge_id="old-match",
        topic="ZEBRA_MARKER",
        content="ZEBRA_MARKER explanation",
        updated_at=datetime(2020, 1, 1),
    )
    db.add(relevant)
    for index in range(20):
        db.add(
            models.ExplanationKnowledge(
                knowledge_id=f"recent-{index}",
                topic="other",
                content="unrelated knowledge",
            )
        )
    db.flush()
    distractor = db.query(models.ExplanationKnowledge).filter_by(knowledge_id="recent-0").one()
    monkeypatch.setattr(service, "_vector_query", lambda *args: [distractor])
    results = service.retrieve_explanation_knowledge("general_chat", "ZEBRA_MARKER", top_k=2)
    by_id = {item["knowledge_id"]: item for item in results}
    assert "old-match" in by_id
    assert by_id["old-match"]["retrieval_sources"] == ["keyword"]
    assert by_id["recent-0"]["retrieval_sources"] == ["semantic"]
    monkeypatch.setattr(service, "_vector_query", lambda *args: [])
    offline = service.retrieve_explanation_knowledge("general_chat", "ZEBRA_MARKER", top_k=2)
    assert [item["knowledge_id"] for item in offline] == ["old-match"]


def test_fixture_scoring_uses_same_fusion_and_adjustments():
    records = [
        {"id": "a", "text": "same marker", "recency_score": 0.2, "applicability_adjustment": -0.1},
        {"id": "b", "text": "same marker", "recency_score": 0.9, "applicability_adjustment": 0.1},
        {"id": "c", "text": "unrelated", "recency_score": 1.0, "entity_match": 1.0},
    ]
    ranked, debug = hybrid_rank(records, "same marker")
    expected = sorted(
        records[:2],
        key=lambda item: adjust_memory_score(
            reciprocal_rank_score(item["id"], [{"a": 0, "b": 1}]),
            item["recency_score"],
            item["applicability_adjustment"],
        ),
        reverse=True,
    )
    assert ranked == [item["id"] for item in expected]
    assert debug["evaluation_scope"] == "fixture_scoring_not_database_recall"


def test_lexical_baseline_does_not_pad_with_zero_score_items():
    records = [{"id": "food", "text": "oatmeal"}, {"id": "training", "text": "deadlift"}]
    assert bm25_rank(records, "zzzz-unmatched-token") == []
    assert bm25_rank(records, "oatmeal") == ["food"]


def test_zero_top_k_does_not_query(db, monkeypatch):
    manager = MemoryManager(db, offline_provider())

    def fail(*args):
        raise AssertionError("zero-budget search must not query")

    monkeypatch.setattr(manager, "_semantic_candidates", fail)
    assert manager.search_memories(uuid.uuid4(), "marker", top_k=0) == []
