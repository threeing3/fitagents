import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from algorithm.evaluation.intent_full_chat_replay import replay_case
from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.chat_workout_record import (
    parse_exercise_set_record,
    parse_workout_record,
)
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.model_provider import ModelProvider


@pytest.mark.parametrize(
    "message,status",
    [
        ("我刚完成30分钟哑铃训练，主观强度7分，帮我记录下来，再告诉我明天怎么安排？", "ready"),
        ("我刚完成哑铃训练，帮我记录", "needs_clarification"),
        ("我刚完成-30分钟哑铃训练，帮我记录", "needs_clarification"),
        ("我刚完成1.5分钟哑铃训练，帮我记录", "needs_clarification"),
        ("我刚完成30分钟哑铃训练，主观强度11分，帮我记录", "needs_clarification"),
        ("我朋友刚完成30分钟跑步，帮我记录", "blocked"),
        ("我刚完成30分钟跑步，不要帮我记录", "blocked"),
        ("如果我刚完成30分钟跑步，帮我记录", "blocked"),
        ("我昨天完成30分钟跑步，帮我记录", "ready"),
        ("跑步30分钟该怎么记录？", "not_requested"),
    ],
)
def test_capture_does_not_guess(message, status):
    assert parse_workout_record(message)["status"] == status


@pytest.mark.parametrize(
    "message,status",
    [
        ("别给我新训练计划，只把昨天跳绳十五分钟记下来。", "needs_clarification"),
        ("给我新训练计划，并把昨天跳绳十五分钟记下来。", "needs_clarification"),
        ("别给我训练计划，我刚完成30分钟跑步，请记下来。", "ready"),
        ("我刚完成30分钟跑步，别记下来。", "blocked"),
        ("昨天朋友跳绳十五分钟，帮我记下来。", "blocked"),
    ],
)
def test_record_request_separates_plan_negation_without_guessing(message, status):
    assert parse_workout_record(message)["status"] == status


@pytest.mark.parametrize(
    "message,status",
    [
        ("昨晚深蹲做完三组，请记录", "ready"),
        ("昨晚深蹲做完3组，请记录", "ready"),
        ("朋友昨晚深蹲做完三组，请记录", "blocked"),
        ("如果昨晚深蹲做完三组，请记录", "blocked"),
        ("昨晚深蹲做完三组，不要记录", "not_requested"),
        ("昨晚深蹲做完3.5组，请记录", "needs_clarification"),
        ("昨晚深蹲和卧推做完三组，请记录", "needs_clarification"),
        ("昨晚深蹲做完三组，跑步30分钟，请记录", "needs_clarification"),
    ],
)
def test_exercise_set_record_requires_explicit_unambiguous_facts(message, status):
    assert parse_exercise_set_record(message)["status"] == status


@pytest.mark.parametrize(
    "message,reply",
    [
        ("我刚完成哑铃训练，帮我记录", "尚未记录，请补充"),
        ("我朋友刚完成30分钟跑步，帮我记录", "未写入训练记录"),
    ],
)
def test_chat_clarifies_or_blocks_without_workout_write(tmp_path, message, reply):
    result = replay_case(
        {
            "case_id": "incomplete_or_third_person",
            "message": message,
            "expected": {"workout_log_delta": 0, "plan_delta": 0},
        },
        tmp_path / "agent_logs",
    )
    assert result["passed"]
    assert reply in result["assistant_message"]
    assert result["state_delta"]["workout_log"] == 0


def test_unrelated_plan_negation_still_records_supported_workout(tmp_path):
    result = replay_case(
        {
            "case_id": "plan_negation_record",
            "message": "别给我训练计划，我刚完成30分钟跑步，请记下来。",
            "expected": {"workout_log_delta": 1, "plan_delta": 0},
        },
        tmp_path / "agent_logs",
    )
    assert result["passed"]
    assert result["tool_call_counts"].get("training.log.write") == 1
    assert result["state_delta"]["workout_log"] == 1


def test_unsupported_historical_activity_clarifies_without_write(tmp_path):
    result = replay_case(
        {
            "case_id": "unsupported_historical_record",
            "message": "别给我新训练计划，只把昨天跳绳十五分钟记下来。",
            "expected": {"workout_log_delta": 0, "plan_delta": 0},
        },
        tmp_path / "agent_logs",
    )
    assert result["passed"]
    assert result["tool_call_counts"].get("training.log.write") == 1
    write_call = next(
        call for call in result["persisted"]["tool_calls"] if call["name"] == "training.log.write"
    )
    assert write_call["output"]["status"] == "needs_clarification"
    assert result["state_delta"]["workout_log"] == 0


def test_same_message_key_reuses_committed_record():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    user_id = uuid.uuid4()
    try:
        with Session(engine) as db:
            db.add(models.User(id=user_id, email="capture@example.test", password_hash="none"))
            db.commit()
            service = CoachAgentService(
                db,
                ModelProvider(
                    Settings(
                        _env_file=None,
                        LLM_PROVIDER="offline",
                        EMBEDDING_PROVIDER="offline",
                        USE_PGVECTOR=False,
                    )
                ),
            )
            message = "我刚完成30分钟哑铃训练，主观强度7分，帮我记录"
            first = service._record_chat_workout(user_id, message, "fixed-message-1")
            second = service._record_chat_workout(user_id, message, "fixed-message-1")
            assert first["workout_log_id"] == second["workout_log_id"]
            assert second["idempotent_replay"] is True
            clarification = service._record_chat_workout(
                user_id, "我刚完成哑铃训练，帮我记录", "fixed-message-2"
            )
            assert clarification["status"] == "needs_clarification"
        with Session(engine) as reader:
            assert reader.scalar(select(func.count()).select_from(models.WorkoutLog)) == 1
            assert reader.scalar(select(func.count()).select_from(models.WorkoutSession)) == 1
            log = reader.scalar(select(models.WorkoutLog))
            assert (log.workout_name, log.duration_minutes, log.rpe) == ("哑铃训练", 30, 7)
    finally:
        engine.dispose()
