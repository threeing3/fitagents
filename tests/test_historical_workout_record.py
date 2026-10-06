import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from algorithm.evaluation.intent_full_chat_replay import replay_case
from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.chat_workout_record import parse_workout_record
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.model_provider import ModelProvider


@pytest.mark.parametrize(
    "message,duration,activity",
    [
        ("我昨天完成二十五分钟跳绳，请记录", 25, "跳绳"),
        ("我昨晚做完四十分钟骑行，帮我记录", 40, "骑行"),
        ("我昨天跑完30分钟跑步，请记下来", 30, "跑步"),
    ],
)
def test_explicit_yesterday_record_preserves_date_not_invented_time(message, duration, activity):
    result = parse_workout_record(message)
    assert result["status"] == "ready"
    fields = result["fields"]
    assert fields["duration_minutes"] == duration
    assert fields["workout_name"] == activity
    assert fields["performed_at"].date() == datetime.now(
        timezone(timedelta(hours=8))
    ).date() - timedelta(days=1)
    assert fields["performed_at"].hour == 0
    assert "日期锚点" in fields["notes"]


@pytest.mark.parametrize(
    "message",
    [
        "我朋友昨天完成二十五分钟跳绳，请记录",
        "如果我昨天完成二十五分钟跳绳，请记录",
        "我昨天完成二十五分钟跳绳，不要帮我记录",
        "我前天完成25分钟跳绳，请记录",
        "我昨天和今天完成25分钟跳绳，请记录",
        "我昨天完成25分钟跳绳，明天请记录",
        "我昨天完成25分钟跳绳和跑步，请记录",
        "我昨天完成二十五分钟又练了十分钟跳绳，请记录",
        "我昨天完成两十五分钟跳绳，请记录",
        "我昨天完成0分钟跳绳，请记录",
        "昨天完成25分钟跳绳，请记录",
    ],
)
def test_uncertain_historical_facts_never_become_ready(message):
    assert parse_workout_record(message)["status"] != "ready"


def test_yesterday_uses_business_date_at_utc_boundary():
    result = parse_workout_record(
        "我昨天完成二十分钟游泳，请记录",
        now=datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc),
    )
    assert result["fields"]["performed_at"].isoformat() == "2026-10-01T00:00:00+08:00"


def test_historical_capture_does_not_mix_independent_future_plan_clause():
    result = parse_workout_record("我昨天完成二十五分钟跳绳，请记录，再告诉我明天怎么安排")
    assert result["status"] == "ready"
    assert result["fields"]["duration_minutes"] == 25


def test_explicit_historical_record_through_full_offline_chat(tmp_path):
    result = replay_case(
        {
            "case_id": "explicit_yesterday_jump_rope",
            "message": "我昨天完成二十五分钟跳绳，请记录",
            "expected": {"workout_log_delta": 1, "plan_delta": 0},
        },
        tmp_path / "agent_logs",
    )
    assert result["passed"]
    assert result["tool_call_counts"].get("training.log.write") == 1
    assert "已记录" in result["assistant_message"]


def test_historical_record_persists_expected_fields_and_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    user_id = uuid.uuid4()
    try:
        with Session(engine) as db:
            db.add(models.User(id=user_id, email="history@example.test", password_hash="none"))
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
            message = "我昨天完成二十五分钟跳绳，请记录"
            first = service._record_chat_workout(user_id, message, "historical-1")
            repeat = service._record_chat_workout(user_id, message, "historical-1")
            assert first["status"] == "recorded"
            assert repeat["idempotent_replay"]
            assert first["workout_log_id"] == repeat["workout_log_id"]
        with Session(engine) as reader:
            assert reader.scalar(select(func.count()).select_from(models.WorkoutLog)) == 1
            assert reader.scalar(select(func.count()).select_from(models.WorkoutSession)) == 1
            record = reader.scalar(select(models.WorkoutLog))
            assert record.workout_name == "跳绳"
            assert record.duration_minutes == 25
            assert record.performed_at.date() == datetime.now(
                timezone(timedelta(hours=8))
            ).date() - timedelta(days=1)
            assert "具体时刻未提供" in record.notes
    finally:
        engine.dispose()
