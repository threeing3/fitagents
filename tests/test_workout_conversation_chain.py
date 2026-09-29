"""Actual streaming and non-streaming turns, with fresh database readers."""

import asyncio
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.core.errors import IdempotencyConflictError
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.agent_runtime import LLMPlanner, PlannerDecision
from fast_api.app.services.chat_request_status import get_chat_request_status
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.workout_history_query import history_query


@contextmanager
def conversation(tmp_path, planner_mode="rule"):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="offline",
        EMBEDDING_PROVIDER="offline",
        ADAPTER_INFERENCE_URL=None,
        USE_PGVECTOR=False,
        AGENT_RUNTIME_MODE="code_driven",
        CODE_DRIVEN_PLANNER=planner_mode,
        AGENT_LOG_DIR=str(tmp_path / "agent_logs"),
    )
    user_id = uuid.uuid4()
    with Session(engine) as db:
        db.add(models.User(id=user_id, email="chain@example.test", password_hash="none"))
        db.add(
            models.UserProfile(
                user_id=user_id,
                age=25,
                sex="male",
                height_cm=175,
                weight_kg=70,
                goal="maintenance",
                experience_level="beginner",
                workout_frequency=3,
                equipment_available=["dumbbells"],
                injuries=[],
            )
        )
        db.commit()
        session_id = (
            CoachAgentService(db, ModelProvider(settings))
            .create_session(user_id, "Synthetic", "Chain")
            .id
        )

    async def invoke(service, message, streaming, sid, key):
        if not streaming:
            return await service.handle_chat_message(sid, user_id, message, idempotency_key=key)
        events = [
            json.loads(raw)
            async for raw in service.stream_chat_events(sid, user_id, message, idempotency_key=key)
        ]
        assert not [item for item in events if item["type"] == "error"]
        done = [item for item in events if item["type"] == "done"]
        assert len(done) == 1
        return {
            "assistant_message": "".join(
                item["text"] for item in events if item["type"] == "answer_delta"
            ).strip(),
            "tool_calls": done[0]["tool_calls"],
            "state_updates": done[0]["state_updates"],
            "agent_run_id": uuid.UUID(done[0]["run_id"]),
            "events": events,
        }

    def turn(message, streaming=False, sid=None, key=None):
        with Session(engine) as db:
            service = CoachAgentService(db, ModelProvider(settings))
            result = asyncio.run(invoke(service, message, streaming, sid or session_id, key))
        result["agent_run_id"] = uuid.UUID(str(result["agent_run_id"]))
        with Session(engine) as reader:
            run = reader.get(models.AgentRun, result["agent_run_id"])
            assert not [node for node in run.nodes if node.get("node") == "RuntimeError"]
            replay = reader.scalar(
                select(models.AgentRunReplay).where(
                    models.AgentRunReplay.agent_run_id == result["agent_run_id"]
                )
            )
            assert replay is not None
            assert replay.response_snapshot["assistant_message"] == result["assistant_message"]
            logs = reader.scalars(select(models.WorkoutLog)).all()
            result["workouts"] = [
                {"name": row.workout_name, "minutes": row.duration_minutes} for row in logs
            ]
            calls = reader.scalars(
                select(models.ToolCall).where(
                    models.ToolCall.agent_run_id == result["agent_run_id"]
                )
            ).all()
            assert len(calls) == len(result["tool_calls"])
            assert (
                reader.scalar(
                    select(models.ChatMessage).where(
                        models.ChatMessage.role == "assistant",
                        models.ChatMessage.content == result["assistant_message"],
                    )
                )
                is not None
            )
        return result

    try:
        with (
            patch("fast_api.app.services.coach_agent.get_settings", return_value=settings),
            patch("fast_api.app.services.agent_observability.get_settings", return_value=settings),
        ):
            yield engine, session_id, user_id, turn
    finally:
        engine.dispose()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("combined", [False, True])
def test_history_query_uses_prior_user_record(tmp_path, streaming, combined):
    with conversation(tmp_path) as (engine, _, uid, turn):
        with Session(engine) as db:
            other = models.User(email="other@example.test", password_hash="none")
            db.add(other)
            db.flush()
            previous = models.WorkoutLog(
                user_id=uid,
                workout_name="跑步",
                duration_minutes=23,
                performed_at=datetime.utcnow() - timedelta(days=40),
            )
            db.add(previous)
            db.add(models.WorkoutLog(user_id=other.id, workout_name="跑步", duration_minutes=99))
            db.commit()
            previous_id = str(previous.id)
        message = "查一下我上次跑步多久"
        if combined:
            message = "我刚完成跑步30分钟，帮我记录，再" + message
        result = turn(message, streaming)
        evidence = result["state_updates"]["workout_history"]
        assert evidence["record_id"] == previous_id
        assert evidence["duration_minutes"] == 23
        assert "23分钟" in result["assistant_message"]
        assert "99分钟" not in result["assistant_message"]
        assert len(result["workouts"]) == (3 if combined else 2)
        if combined:
            assert "已记录：跑步，30分钟" in result["assistant_message"]


@pytest.mark.parametrize("streaming", [False, True])
def test_no_history_does_not_treat_current_write_as_previous(tmp_path, streaming):
    with conversation(tmp_path) as (_, _, _, turn):
        result = turn("我刚完成跑步30分钟，帮我记录，再查一下我上次跑步多久", streaming)
        assert result["state_updates"]["workout_history"]["status"] == "not_found"
        assert "没有查到" in result["assistant_message"]
        assert result["workouts"] == [{"name": "跑步", "minutes": 30}]


@pytest.mark.parametrize("message", ["不要查上次跑步", "查一下他上次跑步多久", "我上次跑步30分钟"])
def test_history_query_requires_self_query(message):
    assert history_query(message) is None


@pytest.mark.parametrize("streaming", [False, True])
def test_model_plan_repairs_read_before_write_and_retries_preserve_evidence(
    tmp_path, monkeypatch, streaming
):
    async def faulty_plan(*args, **kwargs):
        order = ["training.log.write", "coach.reply", "training.log.read", "training.log.read"]
        return PlannerDecision(intent="training_log", selected_tools=order, tool_order=order)

    monkeypatch.setattr(LLMPlanner, "plan", faulty_plan)
    with conversation(tmp_path, planner_mode="llm") as (_, _, _, turn):
        message = "我刚完成跑步30分钟，帮我记录，再查一下我上次跑步多久"
        first = turn(message, streaming, key="combined-retry")
        calls = [call["tool_name"] for call in first["tool_calls"]]
        assert calls.count("training.log.read") == 1
        assert calls.index("training.log.read") < calls.index("training.log.write")
        assert first["state_updates"]["planner"]["fallback"] is False
        second = turn(message, streaming, key="combined-retry")
        assert second["agent_run_id"] == first["agent_run_id"]
        assert second["state_updates"]["workout_history"]["status"] == "not_found"
        assert len(second["workouts"]) == 1


@pytest.mark.parametrize("streaming", [False, True])
def test_different_query_activity_does_not_pollute_current_record(tmp_path, streaming):
    with conversation(tmp_path) as (engine, _, uid, turn):
        with Session(engine) as db:
            db.add(models.WorkoutLog(user_id=uid, workout_name="骑行", duration_minutes=42))
            db.commit()
        result = turn("我刚完成跑步30分钟，帮我记录，再查一下我上次骑行多久", streaming)
        assert result["state_updates"]["workout_record"]["status"] == "recorded"
        assert result["state_updates"]["workout_history"]["duration_minutes"] == 42
        assert {"name": "跑步", "minutes": 30} in result["workouts"]


@pytest.mark.parametrize("streaming", [False, True])
def test_followup_resumes_only_unfinished_write_not_completed_query(tmp_path, streaming):
    with conversation(tmp_path) as (engine, sid, uid, turn):
        first = turn("我刚完成跑步，帮我记录，再查一下我上次骑行多久", streaming)
        assert first["state_updates"]["workout_history"]["status"] == "not_found"
        assert first["workouts"] == []
        with Session(engine) as db:
            pending = db.scalar(
                select(models.PendingQuestion).where(
                    models.PendingQuestion.user_id == uid,
                    models.PendingQuestion.session_id == sid,
                    models.PendingQuestion.status == "pending",
                )
            )
            assert "骑行" not in pending.prompt_text
            assert len(pending.answer_json["missing"]) == 1
        second = turn("30分钟", streaming)
        assert second["workouts"] == [{"name": "跑步", "minutes": 30}]
        assert "workout_history" not in second["state_updates"]
        assert not any(call["tool_name"] == "training.log.read" for call in second["tool_calls"])


@pytest.mark.parametrize("streaming", [False, True])
def test_followup_preserves_write_authorization_after_conjunction(tmp_path, streaming):
    with conversation(tmp_path) as (_, _, _, turn):
        first = turn("我刚完成跑步，再帮我记录，再查一下我上次骑行多久", streaming)
        assert first["workouts"] == []
        assert turn("30分钟", streaming)["workouts"] == [{"name": "跑步", "minutes": 30}]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "message",
    [
        "我刚完成跑步30分钟，不要帮我记录，再查一下我上次骑行多久",
        "我朋友刚完成跑步30分钟，帮我记录，再查一下我上次骑行多久",
    ],
)
def test_query_separation_does_not_remove_write_safety_constraints(tmp_path, streaming, message):
    with conversation(tmp_path) as (_, _, _, turn):
        result = turn(message, streaming)
        assert result["workouts"] == []
        assert result["state_updates"]["workout_record"]["status"] == "blocked"
        assert result["state_updates"]["workout_history"]["status"] == "not_found"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("query", ["查一下我上次骑行的记录", "看看我上次骑行的记录"])
def test_record_noun_in_history_query_does_not_pollute_write(tmp_path, streaming, query):
    with conversation(tmp_path) as (_, _, _, turn):
        result = turn("我刚完成跑步30分钟，帮我记录，再" + query, streaming)
        assert result["workouts"] == [{"name": "跑步", "minutes": 30}]
        assert result["state_updates"]["workout_history"]["status"] == "not_found"


@pytest.mark.parametrize("streaming", [False, True])
def test_pure_history_query_reads_without_new_workout(tmp_path, streaming):
    with conversation(tmp_path) as (engine, _, uid, turn):
        with Session(engine) as db:
            db.add(models.WorkoutLog(user_id=uid, workout_name="游泳", duration_minutes=17))
            db.commit()
        result = turn("查一下我上次训练了多久", streaming)
        assert result["workouts"] == [{"name": "游泳", "minutes": 17}]
        assert "17分钟" in result["assistant_message"]
        assert "workout_record" not in result["state_updates"]
        assert not any(call["tool_name"] == "training.log.write" for call in result["tool_calls"])


@pytest.mark.parametrize("streaming", [False, True])
def test_dated_jog_request_persists_matching_plan_and_reuses_it(tmp_path, streaming):
    with conversation(tmp_path) as (engine, _, uid, turn):
        message = "请安排明天慢跑。"
        first = turn(message, streaming)
        assert "明天慢跑" in first["assistant_message"]
        with Session(engine) as db:
            plans = db.scalars(
                select(models.TrainingPlan).where(models.TrainingPlan.user_id == uid)
            ).all()
            assert len(plans) == 1
            assert plans[0].plan_json["request_constraints"] == {
                "target_date": (datetime.now().date() + timedelta(days=1)).isoformat(),
                "exercise_type": "easy_jog",
            }
            assert plans[0].plan_json["training_days"][0]["name"] == "慢跑"
            assert db.get(models.UserProfile, uid).goal == "maintenance"
            first_id = plans[0].id
        second = turn(message, streaming)
        assert "明天慢跑" in second["assistant_message"]
        with Session(engine) as db:
            plans = db.scalars(
                select(models.TrainingPlan).where(models.TrainingPlan.user_id == uid)
            ).all()
            assert len(plans) == 1
            assert plans[0].id == first_id


@pytest.mark.parametrize("streaming", [False, True])
def test_dated_jog_change_archives_prior_plan_without_changing_fitness_goal(tmp_path, streaming):
    with conversation(tmp_path) as (engine, _, uid, turn):
        first = turn("请安排明天慢跑。", streaming)
        assert "明天慢跑" in first["assistant_message"]
        changed = turn("改成后天慢跑，请安排。", streaming)
        assert "后天慢跑" in changed["assistant_message"]
        with Session(engine) as db:
            plans = db.scalars(
                select(models.TrainingPlan).where(models.TrainingPlan.user_id == uid)
            ).all()
            assert len(plans) == 2
            assert sorted(plan.status for plan in plans) == ["active", "archived"]
            active = next(plan for plan in plans if plan.status == "active")
            assert (
                active.plan_json["request_constraints"]["target_date"]
                == (datetime.now().date() + timedelta(days=2)).isoformat()
            )
            assert db.get(models.UserProfile, uid).goal == "maintenance"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("self_symptom", [False, True])
@pytest.mark.parametrize("planner_mode", ["rule", "llm"])
def test_risk_owner_controls_actual_plan_generation(
    tmp_path, streaming, self_symptom, planner_mode, monkeypatch, record_property
):
    async def faulty_plan(*args, **kwargs):
        if self_symptom:
            return PlannerDecision(
                intent="training_plan",
                selected_tools=["plan.generate", "coach.reply"],
                tool_order=["plan.generate", "coach.reply"],
            )
        return PlannerDecision(
            intent="injury_or_risk",
            selected_tools=["coach.reply"],
            tool_order=["coach.reply"],
            safety_level="high",
        )

    if planner_mode == "llm":
        monkeypatch.setattr(LLMPlanner, "plan", faulty_plan)
    with conversation(tmp_path, planner_mode=planner_mode) as (engine, _, uid, turn):
        message = (
            "朋友胸闷，我也胸闷。请给我制定本周训练计划。"
            if self_symptom
            else "是朋友胸口闷，不是我；我没有胸闷和呼吸困难。请给我制定本周训练计划。"
        )
        result = turn(message, streaming)
        record_property("trajectory", json.dumps(result, ensure_ascii=False, default=str))
        route = (
            next(item for item in result["events"] if item["type"] == "runtime_route")
            if streaming
            else result["runtime_route"]
        )
        decision = route["intent_decision"]
        assert decision["risk"]["level"] == ("high" if self_symptom else "low")
        assert bool(decision["risk"]["evidence"]) is self_symptom
        if planner_mode == "llm":
            assert result["state_updates"]["planner"]["fallback"] is False
        with Session(engine) as db:
            plans = db.scalars(
                select(models.TrainingPlan).where(models.TrainingPlan.user_id == uid)
            ).all()
            assert len(plans) == (0 if self_symptom else 1)
            if not self_symptom:
                assert (
                    db.scalars(select(models.RiskNote).where(models.RiskNote.user_id == uid)).all()
                    == []
                )


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("self_symptom", [False, True])
def test_sibling_symptom_scope_controls_actual_plan(tmp_path, streaming, self_symptom):
    with conversation(tmp_path) as (engine, _, uid, turn):
        message = (
            "我今天膝盖疼，妹妹没受伤。请给我制定本周训练计划。"
            if self_symptom
            else "妹妹今天膝盖疼，我本人没受伤。请给我制定本周训练计划。"
        )
        result = turn(message, streaming)
        route = (
            next(item for item in result["events"] if item["type"] == "runtime_route")
            if streaming
            else result["runtime_route"]
        )
        assert route["intent_decision"]["risk"]["level"] == ("medium" if self_symptom else "low")
        with Session(engine) as db:
            plans = db.scalars(
                select(models.TrainingPlan).where(models.TrainingPlan.user_id == uid)
            ).all()
            assert len(plans) == (0 if self_symptom else 1)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("planner_mode", ["rule", "llm"])
def test_exercise_sets_query_before_write_with_persisted_group_count(
    tmp_path, streaming, planner_mode, monkeypatch
):
    async def omitted_plan(*args, **kwargs):
        return PlannerDecision(
            intent="general_chat", selected_tools=["coach.reply"], tool_order=["coach.reply"]
        )

    if planner_mode == "llm":
        monkeypatch.setattr(LLMPlanner, "plan", omitted_plan)
    with conversation(tmp_path, planner_mode=planner_mode) as (engine, _, uid, turn):
        with Session(engine) as db:
            old = models.WorkoutSession(
                user_id=uid,
                session_date=(datetime.now() - timedelta(days=7)).date(),
                session_name="深蹲训练",
            )
            db.add(old)
            db.flush()
            old_id = old.id
            for set_index in (1, 2):
                db.add(
                    models.ExerciseLog(
                        user_id=uid,
                        session_id=old.id,
                        exercise_name="深蹲",
                        set_index=set_index,
                        completed=True,
                    )
                )
            db.commit()
        message = "昨晚深蹲做完三组，请记录；顺便告诉我上次深蹲做了几组。"
        result = turn(message, streaming, key="set-query-and-write")
        calls = [call["tool_name"] for call in result["tool_calls"]]
        assert calls.index("training.log.read") < calls.index("training.log.write")
        if planner_mode == "llm":
            assert result["state_updates"]["planner"]["fallback"] is False
        assert result["state_updates"]["workout_history"]["completed_sets"] == 2
        assert "2组" in result["assistant_message"]
        assert "已记录：深蹲，3组" in result["assistant_message"]
        with Session(engine) as db:
            sessions = db.scalars(
                select(models.WorkoutSession).where(
                    models.WorkoutSession.user_id == uid,
                )
            ).all()
            assert len(sessions) == 2
            new = next(item for item in sessions if item.id != old_id)
            groups = db.scalars(
                select(models.ExerciseLog).where(
                    models.ExerciseLog.user_id == uid,
                    models.ExerciseLog.session_id == new.id,
                )
            ).all()
            assert {item.set_index for item in groups} == {1, 2, 3}
            assert new.session_date == (datetime.now() - timedelta(days=1)).date()
        replay = turn(message, streaming, key="set-query-and-write")
        assert replay["agent_run_id"] == result["agent_run_id"]
        assert replay["state_updates"]["workout_history"]["completed_sets"] == 2
        assert len(replay["workouts"]) == 1


@pytest.mark.parametrize("streaming", [False, True])
def test_exercise_sets_pure_query_has_no_write_and_is_user_scoped(tmp_path, streaming):
    with conversation(tmp_path) as (engine, _, uid, turn):
        with Session(engine) as db:
            other = models.User(email="other-sets@example.test", password_hash="none")
            db.add(other)
            db.flush()
            session = models.WorkoutSession(user_id=other.id, session_name="深蹲训练")
            db.add(session)
            db.flush()
            for set_index in (1, 2, 3, 4):
                db.add(
                    models.ExerciseLog(
                        user_id=other.id,
                        session_id=session.id,
                        exercise_name="深蹲",
                        set_index=set_index,
                        completed=True,
                    )
                )
            db.commit()
        result = turn("你还记得我上次深蹲做了几组吗？我只是查询，不要新增记录。", streaming)
        assert result["state_updates"]["workout_history"]["status"] == "not_found"
        assert "4组" not in result["assistant_message"]
        assert "workout_record" not in result["state_updates"]
        assert not any(call["tool_name"] == "training.log.write" for call in result["tool_calls"])
        assert result["workouts"] == []


@pytest.mark.parametrize("streaming", [False, True])
def test_exercise_weight_query_reads_completed_user_sets_without_writing(tmp_path, streaming):
    with conversation(tmp_path) as (engine, _, uid, turn):
        with Session(engine) as db:
            old = models.WorkoutSession(user_id=uid, session_name="卧推训练")
            db.add(old)
            db.flush()
            for index, weight in ((1, 50.0), (2, 60.0), (3, None)):
                db.add(
                    models.ExerciseLog(
                        user_id=uid,
                        session_id=old.id,
                        exercise_name="卧推",
                        set_index=index,
                        weight=weight,
                        completed=True,
                    )
                )
            db.commit()
        result = turn("请查一下我上次卧推用了多少公斤；这次不要新增训练记录。", streaming)
        assert result["state_updates"]["workout_history"]["weights_kg"] == [50.0, 60.0]
        assert "50、60公斤" in result["assistant_message"]
        assert not any(call["tool_name"] == "training.log.write" for call in result["tool_calls"])
        assert result["workouts"] == []


@pytest.mark.parametrize("streaming", [False, True])
def test_elliptical_sets_query_is_read_before_new_record(tmp_path, streaming):
    with conversation(tmp_path) as (engine, _, uid, turn):
        with Session(engine) as db:
            old = models.WorkoutSession(user_id=uid, session_name="深蹲训练")
            db.add(old)
            db.flush()
            old_id = old.id
            for index in (1, 2):
                db.add(
                    models.ExerciseLog(
                        user_id=uid,
                        session_id=old.id,
                        exercise_name="深蹲",
                        set_index=index,
                        completed=True,
                    )
                )
            db.commit()
        result = turn("今天深蹲做了五组，请记下来；再告诉我上次做了几组。", streaming)
        calls = [call["tool_name"] for call in result["tool_calls"]]
        assert calls.index("training.log.read") < calls.index("training.log.write")
        assert result["state_updates"]["workout_history"]["completed_sets"] == 2
        assert "2组" in result["assistant_message"]
        assert "已记录：深蹲，5组" in result["assistant_message"]
        with Session(engine) as db:
            sessions = db.scalars(
                select(models.WorkoutSession).where(models.WorkoutSession.user_id == uid)
            ).all()
            assert len(sessions) == 2
            new_session = next(item for item in sessions if item.id != old_id)
            assert (
                len(
                    db.scalars(
                        select(models.ExerciseLog).where(
                            models.ExerciseLog.session_id == new_session.id
                        )
                    ).all()
                )
                == 5
            )


@pytest.mark.parametrize("streaming", [False, True])
def test_goal_correction_chat_invalidates_old_memory(tmp_path, streaming):
    with conversation(tmp_path) as (engine, _, uid, turn):
        with Session(engine) as db:
            profile = db.get(models.UserProfile, uid)
            profile.goal = "fat_loss"
            provider = ModelProvider(
                Settings(
                    _env_file=None,
                    LLM_PROVIDER="offline",
                    EMBEDDING_PROVIDER="offline",
                    USE_PGVECTOR=False,
                )
            )
            old = MemoryManager(db, provider).retain_memory(
                uid, "用户目标是减脂。", "world", "user_profile_fact", category="profile"
            )
            db.commit()
            old_id = old.id
        result = turn("档案里把我的目标写成了减脂，这是错的；请改为增肌。", streaming)
        assert result["state_updates"]["profile_updates"]["goal"] == "muscle_gain"
        assert {item["field"] for item in result["state_updates"]["corrections"]} == {"goal"}
        calls = [call["tool_name"] for call in result["tool_calls"]]
        assert calls.index("memory.verify") < calls.index("memory.write")
        with Session(engine) as db:
            assert db.get(models.UserProfile, uid).goal == "muscle_gain"
            assert db.get(models.LongTermMemory, old_id).status == "superseded"
            active = db.scalars(
                select(models.LongTermMemory).where(
                    models.LongTermMemory.user_id == uid,
                    models.LongTermMemory.status == "active",
                    models.LongTermMemory.memory_type != "correction",
                )
            ).all()
            assert not any("减脂" in memory.content for memory in active)


@pytest.mark.parametrize("streaming", [False, True])
def test_duration_followup_completes_original_record(tmp_path, streaming, record_property):
    with conversation(tmp_path) as (_, _, _, turn):
        first = turn("我刚完成哑铃训练，帮我记录", streaming)
        assert first["workouts"] == []
        assert "尚未记录" in first["assistant_message"]
        second = turn("30分钟", streaming)
        assert second["workouts"] == [{"name": "哑铃训练", "minutes": 30}]
        assert "已记录：哑铃训练，30分钟" in second["assistant_message"]
        assert second["state_updates"]["followup_resolution"]["resolved"]
        third = turn("30分钟", streaming)
        assert len(third["workouts"]) == 1
        record_property(
            "trajectory", json.dumps([first, second, third], ensure_ascii=False, default=str)
        )


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("interruption", ["取消，不要记录了", "今天吃什么比较好？"])
def test_cancel_or_topic_change_does_not_reuse_pending_write(tmp_path, streaming, interruption):
    with conversation(tmp_path) as (_, _, _, turn):
        turn("我刚完成哑铃训练，帮我记录", streaming)
        turn(interruption, streaming)
        assert turn("30分钟", streaming)["workouts"] == []


def test_expired_question_cannot_authorize_write(tmp_path):
    with conversation(tmp_path) as (engine, _, _, turn):
        turn("我刚完成哑铃训练，帮我记录")
        with Session(engine) as db:
            pending = db.scalar(
                select(models.PendingQuestion).where(
                    models.PendingQuestion.question_type == "workout_record"
                )
            )
            pending.expires_at = datetime.utcnow() - timedelta(minutes=1)
            db.commit()
        assert turn("30分钟")["workouts"] == []


@pytest.mark.parametrize("streaming", [False, True])
def test_invalid_duration_keeps_original_task_for_valid_answer(tmp_path, streaming):
    with conversation(tmp_path) as (_, _, _, turn):
        turn("我刚完成哑铃训练，帮我记录", streaming)
        invalid = turn("-30分钟", streaming)
        assert invalid["workouts"] == []
        assert "尚未记录" in invalid["assistant_message"]
        assert turn("30分钟", streaming)["workouts"] == [{"name": "哑铃训练", "minutes": 30}]


def test_followup_is_scoped_to_its_conversation(tmp_path):
    with conversation(tmp_path) as (engine, _, user_id, turn):
        turn("我刚完成哑铃训练，帮我记录")
        with Session(engine) as db:
            other = models.ConversationSession(user_id=user_id, title="Other conversation")
            db.add(other)
            db.commit()
            other_id = other.id
        assert turn("30分钟", sid=other_id)["workouts"] == []
        assert len(turn("30分钟")["workouts"]) == 1


def test_clarification_replaces_invalid_duration_in_original_message(tmp_path):
    with conversation(tmp_path) as (_, _, _, turn):
        turn("我刚完成-30分钟哑铃训练，帮我记录")
        assert turn("30分钟")["workouts"] == [{"name": "哑铃训练", "minutes": 30}]


def test_clarification_replaces_invalid_intensity(tmp_path):
    with conversation(tmp_path) as (engine, _, _, turn):
        turn("我刚完成30分钟哑铃训练，主观强度11分，帮我记录")
        assert len(turn("主观强度7分")["workouts"]) == 1
        with Session(engine) as reader:
            assert reader.scalar(select(models.WorkoutLog)).rpe == 7


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("selection", ["omitted", "late_duplicate"])
def test_model_planner_cannot_omit_or_delay_registered_record(
    tmp_path, monkeypatch, streaming, selection
):
    async def faulty_plan(*args, **kwargs):
        order = ["coach.reply", "response.persist"]
        if selection == "late_duplicate":
            order += ["training.log.write", "training.log.write"]
        return PlannerDecision(intent="training_log", selected_tools=order, tool_order=order)

    monkeypatch.setattr(LLMPlanner, "plan", faulty_plan)
    with conversation(tmp_path, planner_mode="llm") as (_, _, _, turn):
        result = turn("我刚完成30分钟哑铃训练，帮我记录", streaming)
        assert result["workouts"] == [{"name": "哑铃训练", "minutes": 30}]
        calls = [call["tool_name"] for call in result["tool_calls"]]
        assert calls.count("training.log.write") == 1
        assert calls.index("training.log.write") < calls.index("context.build")
        assert "已记录" in result["assistant_message"]
        assert result["state_updates"]["planner"]["fallback"] is False


@pytest.mark.parametrize("streaming", [False, True])
def test_model_planner_cannot_invent_unregistered_workout_write(tmp_path, monkeypatch, streaming):
    async def unsafe_plan(*args, **kwargs):
        return PlannerDecision(
            selected_tools=["training.log.write"], tool_order=["training.log.write"]
        )

    monkeypatch.setattr(LLMPlanner, "plan", unsafe_plan)
    with conversation(tmp_path, planner_mode="llm") as (_, _, _, turn):
        result = turn("力量训练后休息多久？", streaming)
        assert result["workouts"] == []
        assert not any(call["tool_name"] == "training.log.write" for call in result["tool_calls"])
        assert result["state_updates"]["planner"]["fallback"] is True


@pytest.mark.parametrize("streaming", [False, True])
def test_completed_request_replays_after_fresh_service_without_writing(tmp_path, streaming):
    with conversation(tmp_path) as (engine, sid, uid, turn):
        message = "我刚完成30分钟哑铃训练，帮我记录"
        first = turn(message, streaming, key="network-retry-1")
        second = turn(message, streaming, key="network-retry-1")
        assert first["agent_run_id"] == second["agent_run_id"]
        assert second["assistant_message"] == first["assistant_message"]
        assert len(second["workouts"]) == 1
        with Session(engine) as reader:
            status = get_chat_request_status(reader, uid, sid, "network-retry-1")
            assert status["status"] == "completed"
            assert status["assistant_message"] == first["assistant_message"]
            assert len(status["confirmed_writes"]) == 1
            assert len(reader.scalars(select(models.ChatMessage)).all()) == 2
            assert len(reader.scalars(select(models.AgentRun)).all()) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "message,expected_writes",
    [
        ("我刚完成跑步30分钟。", 0),
        ("我刚完成跑步30分钟，已经记录过了。", 0),
        ("我刚完成跑步30分钟，不要帮我记录。", 0),
        ("我刚完成跑步30分钟，帮我记录。", 1),
        ("我刚完成跑步30分钟，请记录。", 1),
        ("我刚完成跑步30分钟，请记下来。", 1),
    ],
)
def test_record_speech_act_controls_persisted_workout(
    tmp_path, streaming, message, expected_writes, record_property
):
    with conversation(tmp_path) as (_, _, _, turn):
        result = turn(message, streaming, key="speech-act-boundary")
        record_property("trajectory", json.dumps(result, ensure_ascii=False, default=str))
        writes = [c for c in result["tool_calls"] if c["tool_name"] == "training.log.write"]
        committed = [c for c in writes if c.get("output", {}).get("status") == "recorded"]
        assert len(committed) == expected_writes
        if not expected_writes:
            assert all(c.get("output", {}).get("status") == "blocked" for c in writes)
        assert len(result["workouts"]) == expected_writes
        if expected_writes:
            assert result["workouts"] == [{"name": "跑步", "minutes": 30}]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "message",
    [
        "我刚训练到一半突然喘不上气，请告诉我该怎么办。",
        "我现在胸口发紧，明天的慢跑先别安排；这种情况该怎么处理？",
    ],
)
def test_acute_red_flag_reply_persists_without_training_or_plan(tmp_path, streaming, message):
    with conversation(tmp_path) as (engine, _, uid, turn):
        result = turn(message, streaming, key="acute-red-flag")
        assert "停止运动" in result["assistant_message"]
        assert "当地急救电话" in result["assistant_message"]
        assert result["workouts"] == []
        with Session(engine) as reader:
            plans = reader.scalars(
                select(models.TrainingPlan).where(models.TrainingPlan.user_id == uid)
            ).all()
            assert plans == []


def test_request_key_cannot_be_reused_for_changed_content(tmp_path):
    with conversation(tmp_path) as (_, _, _, turn):
        turn("我刚完成30分钟哑铃训练，帮我记录", key="fixed-request")
        with pytest.raises(IdempotencyConflictError):
            turn("我刚完成60分钟哑铃训练，帮我记录", key="fixed-request")


def test_interrupted_stream_keeps_request_reserved_after_write(tmp_path):
    with conversation(tmp_path) as (engine, sid, uid, _):
        message = "我刚完成30分钟哑铃训练，帮我记录"
        provider = ModelProvider(
            Settings(
                _env_file=None,
                LLM_PROVIDER="offline",
                EMBEDDING_PROVIDER="offline",
                ADAPTER_INFERENCE_URL=None,
            )
        )
        with Session(engine) as db:
            service = CoachAgentService(db, provider)

            async def interrupt_after_write():
                stream = service.stream_chat_events(
                    sid, uid, message, idempotency_key="interrupted"
                )
                async for _ in stream:
                    if db.scalar(select(models.WorkoutLog)) is not None:
                        await stream.aclose()
                        return
                raise AssertionError("No committed write observed")

            asyncio.run(interrupt_after_write())
        with Session(engine) as db:
            service = CoachAgentService(db, provider)

            async def retry():
                return [
                    json.loads(raw)
                    async for raw in service.stream_chat_events(
                        sid, uid, message, idempotency_key="interrupted"
                    )
                ]

            events = asyncio.run(retry())
            assert events[0]["code"] == "idempotency_conflict"
            assert not any(item["type"] == "done" for item in events)
            assert len(db.scalars(select(models.WorkoutLog)).all()) == 1
        with Session(engine) as reader:
            before = len(reader.scalars(select(models.ChatMessage)).all())
            status = get_chat_request_status(reader, uid, sid, "interrupted")
            assert status["status"] == "unconfirmed"
            assert status["may_repeat_writes"] is False
            assert len(status["confirmed_writes"]) == 1
            assert status["confirmed_writes"][0]["duration_minutes"] == 30
            assert len(reader.scalars(select(models.ChatMessage)).all()) == before
            assert len(reader.scalars(select(models.WorkoutLog)).all()) == 1


def test_request_status_does_not_infer_old_or_unrelated_execution(tmp_path):
    with conversation(tmp_path) as (engine, sid, uid, turn):
        turn("我刚完成跑步30分钟，帮我记录", key="actual-write")
        with Session(engine) as db:
            other = models.User(email="status-other@example.test", password_hash="none")
            db.add(other)
            db.flush()
            other_session = models.ConversationSession(user_id=other.id, title="Other")
            same_user_session = models.ConversationSession(user_id=uid, title="Same user")
            db.add_all([other_session, same_user_session])
            db.add(
                models.IdempotencyRecord(
                    user_id=uid,
                    operation="chat",
                    idempotency_key="old-unlinked",
                    request_json={
                        "session_id": str(sid),
                        "message": "我刚完成跑步30分钟，帮我记录",
                    },
                    status="processing",
                    response_json={},
                )
            )
            db.commit()
            old = get_chat_request_status(db, uid, sid, "old-unlinked")
            assert old["status"] == "unconfirmed"
            assert old["confirmed_writes"] == []
            assert (
                get_chat_request_status(db, uid, same_user_session.id, "actual-write")["status"]
                == "not_found"
            )
            assert (
                get_chat_request_status(db, other.id, other_session.id, "actual-write")["status"]
                == "not_found"
            )
            with pytest.raises(ValueError):
                get_chat_request_status(db, other.id, sid, "actual-write")


def test_request_status_does_not_call_runtime_error_success(tmp_path, monkeypatch):
    async def fail_reply(*args, **kwargs):
        raise RuntimeError("Synthetic reply failure")

    monkeypatch.setattr(CoachAgentService, "_coaching_reply", fail_reply)
    with conversation(tmp_path) as (engine, sid, uid, _):
        with Session(engine) as db:
            service = CoachAgentService(
                db,
                ModelProvider(
                    Settings(
                        _env_file=None,
                        LLM_PROVIDER="offline",
                        EMBEDDING_PROVIDER="offline",
                        ADAPTER_INFERENCE_URL=None,
                    )
                ),
            )
            asyncio.run(
                service.handle_chat_message(
                    sid, uid, "我刚完成跑步30分钟，帮我记录", idempotency_key="failed-reply"
                )
            )
        with Session(engine) as reader:
            status = get_chat_request_status(reader, uid, sid, "failed-reply")
            assert status["status"] == "failed"
            assert len(status["confirmed_writes"]) == 1
