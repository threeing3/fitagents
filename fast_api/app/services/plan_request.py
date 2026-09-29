"""Extract a narrow, explicit workout request from the current user turn."""

import re
from datetime import date, timedelta


def parse_plan_request(message: str, *, today: date | None = None) -> dict[str, str] | None:
    """Recognize a dated jog request; never infer it from older context or quotes."""
    today = today or date.today()
    clauses = re.split(r"[；;。！？!?]", message)
    for clause in clauses:
        if not re.search(r"请安排|帮我安排|给我安排|制定|生成|计划", clause):
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
