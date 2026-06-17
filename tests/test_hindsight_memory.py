import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.context_builder import ContextBuilder
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.reflection_service import ReflectionService


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_retain_hindsight_memory():
    db = make_db()
    user_id = uuid.uuid4()
    memory = MemoryManager(db).retain_memory(
        user_id=user_id,
        content="用户甲亢正在服用赛治，训练需要避免高强度 HIIT。",
        memory_network="world",
        fact_kind="health_fact",
        category="risk",
        evidence=[{"table": "chat_messages", "id": "msg-1"}],
    )

    assert memory.memory_network == "world"
    assert memory.fact_kind == "health_fact"
    assert any(entity["canonical"] == "hyperthyroidism" for entity in memory.entities)
    assert any(entity["canonical"] == "methimazole" for entity in memory.entities)
    assert memory.evidence == [{"table": "chat_messages", "id": "msg-1"}]


def test_search_memory_by_network():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    manager.retain_memory(user_id, "用户喜欢外卖减脂方案。", "world", "nutrition_event", category="nutrition")
    manager.retain_memory(user_id, "Agent 曾建议保守进阶。", "experience", "agent_action", category="decision")

    results = manager.search_memories(user_id, "建议", memory_network="experience")

    assert len(results) == 1
    assert results[0].memory_network == "experience"


def test_search_memory_entity_priority():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    manager.retain_memory(
        user_id,
        "用户偏好哑铃训练。",
        "world",
        "preference",
        category="preference",
        importance_score=0.95,
    )
    risk = manager.retain_memory(
        user_id,
        "用户甲亢并服用赛治，胸闷时需要停止训练。",
        "world",
        "health_fact",
        category="risk",
        importance_score=0.6,
    )

    results = manager.search_memories(user_id, "甲亢 赛治 胸闷", top_k=2)

    assert results[0].id == risk.id


def test_search_memory_vector_match_without_keyword_rank(monkeypatch):
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    memory = manager.retain_memory(
        user_id,
        "User prefers calm evening mobility work.",
        "world",
        "preference",
        category="preference",
        importance_score=0.4,
    )

    monkeypatch.setattr(manager, "_semantic_candidates", lambda query, filters, top_k: [memory])

    results = manager.search_memories(user_id, "zzzz-no-lexical-match", top_k=1)

    assert results[0].id == memory.id
    assert results[0].semantic_rank == 1
    assert results[0].keyword_rank is None
    assert "semantic" in results[0].retrieval_debug["sources"]
    assert "keyword" not in results[0].retrieval_debug["sources"]
    assert results[0].final_score is not None


def test_search_memory_keyword_exact_match_gets_keyword_rank():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    manager.retain_memory(
        user_id,
        "User likes general strength training.",
        "world",
        "preference",
        category="preference",
        importance_score=0.95,
    )
    exact = manager.retain_memory(
        user_id,
        "User has a VO2MAX_MARKER preference for aerobic testing.",
        "world",
        "training_fact",
        category="training",
        importance_score=0.2,
    )

    results = manager.search_memories(user_id, "VO2MAX_MARKER", top_k=2)

    assert results[0].id == exact.id
    assert results[0].keyword_rank == 1
    assert "keyword" in results[0].retrieval_debug["sources"]
    assert results[0].final_score is not None


def test_keyword_candidates_prefers_postgres_fts_sql():
    memory = models.LongTermMemory(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        memory_type="training_fact",
        memory_network="world",
        fact_kind="workout_event",
        category="training",
        content="bench press exact keyword",
        summary="bench keyword",
        importance=0.5,
        confidence=0.8,
        source="test",
    )

    class FakeDialect:
        name = "postgresql"

    class FakeBind:
        dialect = FakeDialect()

    class EmptyScalars:
        def __iter__(self):
            return iter([])

    class FakeDB:
        def __init__(self):
            self.statement = None

        def get_bind(self):
            return FakeBind()

        def execute(self, statement):
            self.statement = statement
            return [(memory, 0.9)]

        def scalars(self, statement):
            return EmptyScalars()

    fake_db = FakeDB()
    manager = MemoryManager(fake_db)  # type: ignore[arg-type]

    candidates, scores = manager._keyword_candidates("bench keyword", [models.LongTermMemory.user_id == memory.user_id], 3)

    sql = str(fake_db.statement.compile(dialect=postgresql.dialect()))
    assert candidates == [memory]
    assert scores[memory.id] == 0.9
    assert "to_tsvector" in sql
    assert "plainto_tsquery" in sql
    assert "@@" in sql


def test_search_memory_entity_match_gets_entity_rank_and_risk_weight():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    manager.retain_memory(
        user_id,
        "User prefers high volume bench press blocks.",
        "world",
        "preference",
        category="preference",
        importance_score=0.95,
    )
    medication = manager.retain_memory(
        user_id,
        "User takes methimazole and needs conservative high-intensity training guidance.",
        "world",
        "health_fact",
        category="risk",
        entities=[{"type": "medication", "name": "methimazole", "canonical": "methimazole"}],
        importance_score=0.35,
    )

    results = manager.search_memories(user_id, "methimazole training", top_k=2)

    assert results[0].id == medication.id
    assert results[0].entity_rank == 1
    assert "entity" in results[0].retrieval_debug["sources"]
    assert results[0].retrieval_debug["risk_priority"] > 0
    assert results[0].final_score is not None


def test_search_memory_temporal_range_match_gets_temporal_debug():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    old = manager.retain_memory(
        user_id,
        "User had an old recovery note.",
        "world",
        "recovery_event",
        category="recovery",
        occurred_start=datetime.utcnow() - timedelta(days=30),
    )
    recent = manager.retain_memory(
        user_id,
        "User had a recent recovery note.",
        "world",
        "recovery_event",
        category="recovery",
        occurred_start=datetime.utcnow() - timedelta(days=2),
    )

    results = manager.search_memories(
        user_id,
        "recovery note",
        top_k=5,
        occurred_after=datetime.utcnow() - timedelta(days=7),
        occurred_before=datetime.utcnow() + timedelta(days=1),
    )

    ids = [memory.id for memory in results]
    assert recent.id in ids
    assert old.id not in ids
    matched = next(memory for memory in results if memory.id == recent.id)
    assert matched.temporal_rank == 1
    assert "temporal" in matched.retrieval_debug["sources"]


def test_search_memory_risk_priority_beats_generic_high_importance():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    manager.retain_memory(
        user_id,
        "User generally likes aggressive progression.",
        "world",
        "preference",
        category="preference",
        importance_score=0.95,
    )
    risk = manager.retain_memory(
        user_id,
        "User reported chest tightness during hard intervals.",
        "world",
        "health_fact",
        category="risk",
        importance_score=0.35,
    )

    results = manager.search_memories(user_id, "chest tightness training", top_k=2)

    assert results[0].id == risk.id
    assert results[0].retrieval_debug["risk_priority"] > 0
    assert results[0].final_score > results[1].final_score


def test_reflection_creates_observation_and_opinion():
    db = make_db()
    user_id = uuid.uuid4()
    db.add(models.RecoveryLog(user_id=user_id, log_date=date.today(), sleep_hours=5, fatigue_score=8))
    db.add(models.SymptomLog(user_id=user_id, symptom_date=date.today(), symptom_type="pain", status="active"))
    db.add(models.AgentDecision(
        user_id=user_id,
        decision_type="progression",
        input_summary="用户疲劳高",
        context_used={},
        decision_result="hold_load",
        reason="sleep poor and fatigue high",
        confidence_score=0.8,
    ))
    db.flush()

    result = ReflectionService(db).reflect_user_memory(user_id)

    assert result["created_count"] == 2
    networks = {item["memory_network"] for item in result["memories"]}
    assert networks == {"observation", "opinion"}
    assert all(item["evidence"] for item in result["memories"])
    assert all({"table", "id", "summary", "time"}.issubset(item["evidence"][0]) for item in result["memories"])


def test_opinion_memory_without_evidence_is_rejected():
    db = make_db()
    manager = MemoryManager(db)

    try:
        manager.retain_memory(
            user_id=uuid.uuid4(),
            content="Coach thinks progression is fine.",
            memory_network="opinion",
            fact_kind="coach_opinion",
            category="training",
        )
    except ValueError as exc:
        assert "requires evidence" in str(exc)
    else:
        raise AssertionError("opinion memory without evidence should be rejected")


class GroupedRetrieval:
    def get_core_profile(self, user_id):
        return {}

    def get_memory_catalog(self, user_id, category=None):
        return []

    def get_active_plan(self, user_id):
        return None

    def get_active_risk_notes(self, user_id):
        return []

    def search_relevant_memories(self, user_id, query, top_k=6, category=None):
        return [
            {"memory_network": "world", "summary": "fact"},
            {"memory_network": "experience", "summary": "past action"},
            {"memory_network": "observation", "summary": "pattern"},
            {"memory_network": "opinion", "summary": "coach opinion"},
        ]

    def get_recent_workout_logs(self, user_id, days=14):
        return []

    def get_exercise_history(self, user_id, exercise_name=None):
        return []

    def get_recent_nutrition_summary(self, user_id, days=7):
        return []

    def get_recent_recovery_logs(self, user_id, days=7):
        return []

    def get_recent_symptom_logs(self, user_id, days=14):
        return []


def test_context_builder_groups_hindsight_memories():
    builder = ContextBuilder.__new__(ContextBuilder)
    builder.intent_router = type("Router", (), {"classify": lambda self, message: "general_chat"})()
    builder.retrieval = GroupedRetrieval()
    builder.knowledge = FitnessKnowledgeService(db=None)

    packet = builder.build_context_packet(uuid.uuid4(), "hello")

    assert len(packet["world_memories"]) == 1
    assert len(packet["experience_memories"]) == 1
    assert len(packet["observation_memories"]) == 1
    assert len(packet["opinion_memories"]) == 1
    assert packet["opinion_memories"][0]["evidence_summary"] == "No evidence attached."
    assert len(packet["relevant_memories"]) == 4


def test_context_builder_adds_opinion_evidence_summary():
    class OpinionRetrieval(GroupedRetrieval):
        def search_relevant_memories(self, user_id, query, top_k=6, category=None):
            return [
                {
                    "memory_network": "opinion",
                    "summary": "coach judgment",
                    "evidence": [{"table": "recovery_logs", "id": "r1", "summary": "fatigue high", "time": "2026-06-11"}],
                }
            ]

    builder = ContextBuilder.__new__(ContextBuilder)
    builder.intent_router = type("Router", (), {"classify": lambda self, message: "general_chat"})()
    builder.retrieval = OpinionRetrieval()
    builder.knowledge = FitnessKnowledgeService(db=None)

    packet = builder.build_context_packet(uuid.uuid4(), "hello")

    assert "recovery_logs: fatigue high" in packet["opinion_memories"][0]["evidence_summary"]


def test_correction_goal_fat_loss_to_muscle_gain_creates_link_and_supersedes_old():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    old = manager.retain_memory(user_id, "用户目标是减脂。", "world", "user_profile_fact", category="profile")

    result = manager.handle_correction_flow(user_id, "不对，我目标改了，现在不是减脂，是增肌。", category="profile")

    assert result["correction_detected"] is True
    assert result["memory"].memory_network == "world"
    assert result["memory"].fact_kind == "correction"
    assert old.status == "superseded"
    assert old.valid_until is not None
    link = db.scalar(select(models.MemoryLink).where(models.MemoryLink.target_memory_id == old.id))
    assert link is not None
    assert link.link_type in {"updates", "contradicts"}


def test_add_memory_enters_correction_flow_on_correction_signal():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    old = manager.retain_memory(user_id, "用户目标是减脂。", "world", "user_profile_fact", category="profile")

    memory = manager.add_memory(
        user_id,
        {
            "memory_type": "user_profile_fact",
            "category": "profile",
            "content": "不对，现在不是减脂，目标改了是增肌。",
        },
    )

    assert memory.fact_kind == "correction"
    assert old.status == "superseded"
    assert db.scalar(select(models.MemoryLink).where(models.MemoryLink.target_memory_id == old.id)) is not None


def test_correction_risk_active_to_resolved_supersedes_old_risk():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    old = manager.retain_memory(user_id, "用户膝盖疼痛风险 active。", "world", "health_fact", category="risk")

    result = manager.handle_correction_flow(user_id, "已经好了，医生说膝盖疼痛风险现在不是 active。", category="risk")

    assert result["memory"].category == "risk"
    assert old.status == "superseded"
    link = db.scalar(select(models.MemoryLink).where(models.MemoryLink.target_memory_id == old.id))
    assert link is not None
    assert link.link_type == "contradicts"


def test_correction_nutrition_preference_change_updates_old_memory():
    db = make_db()
    user_id = uuid.uuid4()
    manager = MemoryManager(db)
    old = manager.retain_memory(user_id, "用户饮食偏好是素食。", "world", "nutrition_event", category="nutrition")

    result = manager.handle_correction_flow(user_id, "饮食偏好改了，现在不是素食了，可以吃鱼。", category="nutrition")

    assert result["memory"].category == "nutrition"
    assert old.status == "superseded"
    assert db.scalar(select(models.MemoryCatalog).where(models.MemoryCatalog.user_id == user_id, models.MemoryCatalog.category == "nutrition")) is not None


def test_weekly_reflection_full_week_creates_observations_and_opinion():
    db = make_db()
    user_id = uuid.uuid4()
    week_start = date(2026, 6, 1)
    week_end = date(2026, 6, 7)
    for index in range(4):
        day = week_start + timedelta(days=index)
        db.add(models.WorkoutLog(user_id=user_id, performed_at=datetime.combine(day, datetime.min.time()), workout_name=f"Workout {index}", rpe=7))
        db.add(models.NutritionDailySummary(user_id=user_id, summary_date=day, total_protein_g=120 + index, adherence_score=0.8))
        db.add(models.RecoveryLog(user_id=user_id, log_date=day, sleep_hours=7, fatigue_score=4))
    db.flush()

    result = ReflectionService(db).reflect_weekly(user_id, week_start, week_end)

    fact_kinds = {item["fact_kind"] for item in result["memories"]}
    assert {"weekly_training_observation", "weekly_nutrition_observation", "weekly_recovery_observation", "coach_opinion"}.issubset(fact_kinds)
    assert all(item["evidence"] for item in result["memories"])
    assert all({"summary", "time"}.issubset(item["evidence"][0]) for item in result["memories"])


def test_weekly_reflection_insufficient_data_does_not_create_opinion():
    db = make_db()
    user_id = uuid.uuid4()
    week_start = date(2026, 6, 1)
    week_end = date(2026, 6, 7)
    db.add(models.WorkoutLog(user_id=user_id, performed_at=datetime.combine(week_start, datetime.min.time()), workout_name="One workout", rpe=7))
    db.flush()

    result = ReflectionService(db).reflect_weekly(user_id, week_start, week_end)

    assert "coach_opinion" not in {item["fact_kind"] for item in result["memories"]}
    assert any(item["fact_kind"] == "weekly_training_observation" for item in result["memories"])


def test_reflect_decision_outcomes_creates_strategy_experience_memory():
    db = make_db()
    user_id = uuid.uuid4()
    decision_time = datetime.utcnow() - timedelta(days=3)
    decision = models.AgentDecision(
        user_id=user_id,
        decision_type="training_adjustment",
        input_summary="User reported fatigue; agent suggested conservative progression.",
        context_used={},
        decision_result="reduce load and keep pain-free movement",
        reason="Fatigue was elevated and the next session should protect consistency.",
        confidence_score=0.82,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    db.add(models.WorkoutLog(
        user_id=user_id,
        performed_at=decision_time + timedelta(days=1),
        workout_name="Pain-free lower body",
        rpe=6,
        completion_rate=0.9,
        notes="Completed reduced-load session without pain.",
    ))
    db.add(models.RecoveryLog(
        user_id=user_id,
        log_date=(decision_time + timedelta(days=1)).date(),
        sleep_hours=7.5,
        fatigue_score=4,
    ))
    db.add(models.SymptomLog(
        user_id=user_id,
        symptom_date=(decision_time + timedelta(days=1)).date(),
        symptom_type="pain",
        severity_score=2,
        status="monitoring",
    ))
    db.flush()

    result = ReflectionService(db).reflect_decision_outcomes(user_id)

    assert result["created_count"] == 1
    assert result["outcomes"][0]["outcome_status"] == "improved"
    assert result["outcomes"][0]["metrics"]["avg_completion_rate"] == 0.9
    assert result["memories"][0]["memory_network"] == "experience"
    assert result["memories"][0]["fact_kind"] == "strategy_experience"
    assert any(item["table"] == "decision_outcomes" for item in result["memories"][0]["evidence"])
    memory = db.get(models.LongTermMemory, uuid.UUID(result["memories"][0]["id"]))
    assert memory is not None
    assert "Outcome-aware coaching experience" in memory.content


def test_reflect_decision_outcomes_is_idempotent_for_existing_outcome():
    db = make_db()
    user_id = uuid.uuid4()
    decision_time = datetime.utcnow() - timedelta(days=2)
    decision = models.AgentDecision(
        user_id=user_id,
        decision_type="nutrition_strategy",
        input_summary="User needed a takeout nutrition strategy.",
        context_used={},
        decision_result="use high-protein takeout defaults",
        reason="Protein intake was below target.",
        confidence_score=0.8,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    db.add(models.NutritionDailySummary(
        user_id=user_id,
        summary_date=(decision_time + timedelta(days=1)).date(),
        total_protein_g=135,
        target_protein_g=140,
        adherence_score=0.82,
        summary_text="Hit the takeout protein target.",
    ))
    db.flush()
    service = ReflectionService(db)

    first = service.reflect_decision_outcomes(user_id)
    second = service.reflect_decision_outcomes(user_id)

    assert first["created_count"] == 1
    assert first["outcomes"][0]["outcome_type"] == "nutrition_outcome"
    assert first["memories"][0]["fact_kind"] == "strategy_experience"
    assert second["created_count"] == 0
    assert second["skipped"][0]["reason"] == "outcome_already_exists"


def test_rules_override_opinion_memory():
    service = FitnessKnowledgeService(db=None)
    context = {
        "active_risk_notes": [{"risk_type": "thyroid", "severity_score": 0.9}],
        "opinion_memories": [
            {"memory_network": "opinion", "content": "Agent thinks high intensity may be okay."}
        ],
        "relevant_memories": [{"memory_type": "medical_context", "content": "甲亢"}],
    }

    rules = service.match_decision_rules("training_plan", context)

    assert "rule_medical_hyperthyroid_conservative_001" in [rule["rule_id"] for rule in rules]


def test_coach_prompt_policy_mentions_opinion_memory_evidence_boundary():
    source = Path("fast_api/app/services/coach_agent.py").read_text(encoding="utf-8")

    assert "Opinion memories are not facts" in source
    assert "evidence_summary" in source
    assert "strategy_experience" in source
    assert "failed_strategy" in source
    assert "avoid repeating" in source


def test_local_coaching_reply_includes_strategy_memory_guidance():
    service = CoachAgentService.__new__(CoachAgentService)
    profile = SimpleNamespace(
        goal="muscle_gain",
        weight_kg=80,
        target_calories=2400,
        target_protein_g=160,
        target_carbs_g=260,
        target_fat_g=70,
        equipment_available=["gym"],
    )
    plan = SimpleNamespace(
        plan_json={
            "training_days": [
                {
                    "exercises": [
                        {
                            "name": "Reduced-load squat",
                            "sets": 3,
                            "reps": "6",
                            "rest_seconds": 120,
                        }
                    ]
                }
            ]
        }
    )
    context_packet = {
        "current_request_policy": {"allow_plan_content": True},
        "strategy_memory_guidance": {
            "successful_strategies": [
                {"summary": "Reduced-load training improved completion after fatigue."}
            ],
            "failed_strategies": [
                {"summary": "High-intensity top sets worsened fatigue and completion."}
            ],
        },
    }

    reply = service._local_coaching_fallback(profile, plan, context_packet)

    assert "Strategy memory guidance:" in reply
    assert "Do not reuse any strategy that conflicts with active risk notes or decision rules." in reply
    assert "Reuse prior successful strategy only if similar" in reply
    assert "Reduced-load training improved completion" in reply
    assert "Avoid repeating prior failed strategy" in reply
    assert "High-intensity top sets worsened fatigue" in reply
