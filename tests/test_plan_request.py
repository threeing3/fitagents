from datetime import date

from fast_api.app.services.plan_request import parse_plan_request


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
