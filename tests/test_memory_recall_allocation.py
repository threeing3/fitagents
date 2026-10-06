"""Exercise recall allocation through the real offline database/context path."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.db.database import Base
from fast_api.app.services.context_builder import ContextBuilder, FitnessRetrievalService
from fast_api.app.services.memory_planner import MemoryPlanner, MemoryRecallPlan, MemorySearchSpec
from fast_api.app.services.model_provider import ModelProvider


@pytest.fixture
def builder():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    provider = ModelProvider(
        Settings(_env_file=None, LLM_PROVIDER="offline", EMBEDDING_PROVIDER="offline")
    )
    with Session(engine) as db:
        yield ContextBuilder(db, provider)
    engine.dispose()


@pytest.mark.parametrize(
    ("intent", "category", "need_opinion"),
    [
        ("training_plan", "training", False),
        ("nutrition_advice", "nutrition", False),
        ("weekly_review", "training", False),
        ("memory_query", "training", True),
    ],
)
def test_final_context_keeps_late_recall_branches(builder, intent, category, need_opinion):
    user_id = uuid4()
    manager = builder.retrieval.memory_manager
    required = set()
    for network, kind, count in [
        ("world", "training_fact", 5),
        ("experience", "strategy_experience", 4),
        ("experience", "failed_strategy", 3),
        ("observation", "training_observation", 3),
        ("opinion", "coach_opinion", 2),
    ]:
        for index in range(count):
            manager.retain_memory(
                user_id,
                f"基准任务 {network} {kind} {index}",
                network,
                kind,
                category=category,
                evidence=[{"table": "synthetic", "id": f"{kind}-{index}"}],
            )
        if network == "observation" or kind == "failed_strategy":
            required.add(kind)
    if need_opinion:
        # The broad experience lane does not promise a separate failed-strategy slot.
        required.discard("failed_strategy")
        required.add("coach_opinion")
    packet = builder.build_context_packet(user_id, "基准任务", intent=intent)
    memories = packet["relevant_memories"]
    assert len(memories) <= packet["retrieval_debug"]["memory_top_k"]
    assert required <= {memory["fact_kind"] for memory in memories}
    assert len({memory["id"] for memory in memories}) == len(memories)
    assert any(memory["memory_network"] == "experience" for memory in memories)
    if not need_opinion:
        assert all(memory["memory_network"] != "opinion" for memory in memories)
        assert packet["strategy_memory_guidance"]["failed_strategies"]


def test_risk_fact_survives_final_world_memory_budget(builder):
    user_id = uuid4()
    manager = builder.retrieval.memory_manager
    for index in range(8):
        manager.retain_memory(
            user_id,
            f"训练计划 偏好 {index}",
            "world",
            "preference",
            category="training",
            importance_score=0.9,
        )
    risk = manager.retain_memory(
        user_id,
        "甲亢训练约束：避免高强度训练。",
        "world",
        "health_fact",
        category="risk",
        importance_score=0.2,
    )
    packet = builder.build_context_packet(user_id, "甲亢 训练计划", intent="training_plan")
    assert str(risk.id) in {memory["id"] for memory in packet["world_memories"]}
    assert len(packet["world_memories"]) <= builder.CONTEXT_LIMITS["world_memories"]
    assert packet["world_memories"][0]["id"] == str(risk.id)


def test_correction_and_user_isolation_survive_recall_allocation(builder):
    user_id = uuid4()
    manager = builder.retrieval.memory_manager
    old = manager.retain_memory(
        user_id, "用户喜欢晨练。", "world", "user_preference", category="preference"
    )
    foreign = manager.retain_memory(
        uuid4(), "用户喜欢晨练。", "world", "user_preference", category="preference"
    )
    correction = manager.handle_correction_flow(
        user_id,
        "不对，我训练时间改了，现在不是晨练，是晚练。",
        category="preference",
        corrected_memory_ids=[old.id],
    )["memory"]
    packet = builder.build_context_packet(user_id, "晨练 晚练 时间", intent="memory_query")
    selected_ids = {memory["id"] for memory in packet["relevant_memories"]}
    assert str(correction.id) in selected_ids
    assert str(old.id) not in selected_ids
    assert str(foreign.id) not in selected_ids
    assert old.status == "superseded"


def make_memory(key, network="world"):
    return SimpleNamespace(
        id=key,
        memory_type="training_fact",
        memory_network=network,
        fact_kind="training_fact",
        category="risk" if key == "risk" else "training",
        summary=key,
        content=key,
        importance=0.6,
        confidence=0.8,
        memory_metadata={},
    )


def test_tight_budget_protects_risk_and_merges_duplicate_labels():
    plan = MemoryPlanner().build_plan("training_plan", "pain", None)
    plan = replace(plan, top_k=2)
    shared = make_memory("risk")
    by_kind = {
        ("world", None): [make_memory("fact"), shared],
        ("world", "risk"): [shared],
    }

    class Manager:
        def search_memories(self, user_id, query, **kwargs):
            return by_kind.get((kwargs["memory_network"], kwargs["category"]), [])

    retrieval = FitnessRetrievalService.__new__(FitnessRetrievalService)
    retrieval.memory_manager = Manager()
    result = retrieval.search_planned_memories(uuid4(), "pain", plan)
    assert [memory["id"] for memory in result] == ["risk", "fact"]
    assert result[0]["retrieval_plan_labels"] == ["training_facts", "risk_facts"]


def test_empty_lanes_duplicate_candidates_and_exclusion_do_not_waste_budget():
    shared = make_memory("shared")
    candidates = {
        "world": [shared, make_memory("fact")],
        "experience": [shared, make_memory("experience", "experience")],
        "observation": [],
        "opinion": [make_memory("opinion", "opinion")],
    }

    class Manager:
        def search_memories(self, user_id, query, **kwargs):
            return candidates[kwargs["memory_network"]]

    retrieval = FitnessRetrievalService.__new__(FitnessRetrievalService)
    retrieval.memory_manager = Manager()
    plan = MemoryRecallPlan(
        "general_chat",
        None,
        5,
        [MemorySearchSpec(network, network, memory_network=network) for network in candidates],
        ["opinion"],
        [],
    )
    result = retrieval.search_planned_memories(uuid4(), "query", plan)
    assert {memory["id"] for memory in result} == {"shared", "fact", "experience"}
    assert result[0]["retrieval_plan_labels"] == ["world", "experience"]
