from datetime import date, datetime, timezone

import pytest

from fast_api.app.services.plan_request import date_in_timezone, parse_plan_request


def test_explicit_tomorrow_jog_is_structured():
    assert parse_plan_request(
        "我说‘胸口发紧’是在引用歌词，并不是我的症状；请安排明天慢跑。",
        today=date(2026, 9, 28),
    ) == {"target_date": "2026-09-29", "exercise_type": "easy_jog"}


def test_negation_and_past_date_do_not_create_request():
    today = date(2026, 9, 28)
    assert parse_plan_request("别安排明天慢跑", today=today) is None
    assert parse_plan_request("请安排2026-09-27慢跑", today=today) is None
    assert parse_plan_request("昨天说了明天慢跑", today=today) is None


def test_absolute_date_and_unsupported_activity():
    today = date(2026, 9, 28)
    assert parse_plan_request("请安排2026-10-03慢跑", today=today) == {
        "target_date": "2026-10-03",
        "exercise_type": "easy_jog",
    }
    assert parse_plan_request("请安排明天游泳", today=today) is None


@pytest.mark.parametrize(
    ("text", "today", "expected"),
    [
        ("周五仅慢跑", date(2026, 10, 1), "2026-10-02"),
        ("周五仅慢跑", date(2026, 10, 2), "2026-10-02"),
        ("周五仅慢跑", date(2026, 10, 3), "2026-10-09"),
        ("请安排下周五慢跑", date(2026, 10, 4), "2026-10-09"),
        ("请安排下周五慢跑", date(2026, 10, 1), "2026-10-09"),
        ("请安排本周五慢跑", date(2026, 10, 1), "2026-10-02"),
    ],
)
def test_weekday_request_resolves_against_explicit_today(text, today, expected):
    assert parse_plan_request(text, today=today) == {
        "target_date": expected,
        "exercise_type": "easy_jog",
    }


@pytest.mark.parametrize(
    "text",
    [
        "请安排本周五慢跑",
        "他说‘请安排周五慢跑’",
        "如果请安排周五慢跑会怎样",
        "请安排周五慢跑吗",
        "别安排周五慢跑",
    ],
)
def test_past_explicit_week_quotes_and_questions_do_not_create(text):
    assert parse_plan_request(text, today=date(2026, 10, 3)) is None


def test_same_utc_clock_has_different_user_local_day():
    clock = datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc)
    assert date_in_timezone("Asia/Shanghai", clock) == date(2026, 10, 2)
    assert date_in_timezone("America/Los_Angeles", clock) == date(2026, 10, 1)
    with pytest.raises(ValueError, match="aware clock"):
        date_in_timezone("UTC", clock.replace(tzinfo=None))
