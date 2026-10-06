"""Extract a narrow, explicit workout request from the current user turn."""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def date_in_timezone(name: str, now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("An aware clock is required for user-local dates")
    return current.astimezone(ZoneInfo(name)).date()


def parse_plan_request(message: str, *, today: date | None = None) -> dict[str, str] | None:
    """Resolve jog dates against an explicit caller clock; bare weekdays mean next occurrence."""
    today = today or date.today()
    clauses = re.split(r"[；;。！？!?]", message)
    for clause in clauses:
        if re.search(r"[‘’“”\"']|如果|假如|他说|她说|是否|能否|吗", clause):
            continue
        if not re.search(
            r"请安排|帮我安排|给我安排|制定|生成|计划|(?:周|星期)[一二三四五六日天].*仅慢跑", clause
        ):
            continue
        if re.search(r"不(?:要|用|必|需要)?(?:安排|制定|生成)|别(?:安排|制定|生成)", clause):
            continue
        if "慢跑" not in clause:
            continue
        if "后天" in clause:
            target_date = today + timedelta(days=2)
        elif "明天" in clause:
            target_date = today + timedelta(days=1)
        elif "今天" in clause:
            target_date = today
        else:
            weekday = re.search(r"(下|本|这)?(?:周|星期)([一二三四五六日天])", clause)
            if weekday:
                index = "一二三四五六日".index(weekday[2].replace("天", "日"))
                if weekday[1] == "下":
                    target_date = today + timedelta(days=7 - today.weekday() + index)
                elif weekday[1] in {"本", "这"}:
                    target_date = today + timedelta(days=index - today.weekday())
                else:
                    target_date = today + timedelta(days=(index - today.weekday()) % 7)
                if target_date < today:
                    continue
                return {"target_date": target_date.isoformat(), "exercise_type": "easy_jog"}
            match = re.search(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", clause)
            if match is None:
                continue
            try:
                target_date = date(*map(int, match.groups()))
            except ValueError:
                continue
            if target_date < today:
                continue
        return {"target_date": target_date.isoformat(), "exercise_type": "easy_jog"}
    return None
