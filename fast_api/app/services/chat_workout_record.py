"""Conservative single-turn capture: ambiguous facts never become workout writes."""

import re
from datetime import datetime, time, timedelta, timezone

from fast_api.app.services.exercise_constraints import exercise_exclusions
from fast_api.app.services.workout_history_query import history_query

RECORD_REQUEST = re.compile(r"帮我记录|帮我记下|请记录|记下来|记录下来")


def record_task_text(message: str) -> str:
    """Separate explicit history-query clauses from current workout facts.

    Keep record authorization and record negation; a separate negated plan clause
    must not block a requested workout record. The raw message stays persisted.
    """
    retained = []
    for clause in re.split(r"[，,。！？!?；;]|再(?=(?:帮我)?(?:查|看看|看一下))", message):
        if (
            exercise_exclusions(clause)
            and not RECORD_REQUEST.search(clause)
            and not re.search(r"完成|做完|练完|跑完|记录|记下", clause)
        ):
            continue
        negated_plan_only = (
            re.search(r"(?:不要|不需要|不用|别|先别).*?(?:训练计划|健身计划|计划)", clause)
            and not RECORD_REQUEST.search(clause)
            and not re.search(r"(?:不要|别|不用).*?(?:记录|记下)", clause)
        )
        if negated_plan_only:
            continue
        future_plan_only = (
            re.search(r"明天|后天|下周", clause)
            and re.search(r"安排|计划", clause)
            and not RECORD_REQUEST.search(clause)
            and not re.search(r"完成|做完|练完|跑完|(?:不要|别|不用).*?(?:记录|记下)", clause)
        )
        if future_plan_only:
            continue
        if history_query(clause) is not None and not re.search(
            r"帮我记录|帮我记下|记下来|记录下来|(?:不要|别|不用).*记录"
            r"|我(?:今天|刚刚|刚|已经|刚才)?(?:完成|做完|做了|练完|跑完)",
            clause,
        ):
            continue
        if clause.strip():
            retained.append(clause.strip())
    return "，".join(retained)


def parse_exercise_set_record(message: str) -> dict:
    """Recognize one explicit completed exercise with a bounded set count."""
    if not RECORD_REQUEST.search(message):
        return {"status": "not_requested"}
    text = record_task_text(message)
    exercises = [name for name in ("深蹲", "卧推", "硬拉") if name in text]
    if not exercises or "组" not in text:
        return {"status": "not_requested"}
    if re.search(r"不要|不用|别|朋友|他|她|如果|假如|打算|准备", text):
        return {"status": "blocked", "reply": "这条消息的记录归属或授权不明确，未写入训练记录。"}
    if (
        len(exercises) != 1
        or any(word in text for word in ("跑步", "骑行", "游泳", "哑铃训练", "力量训练"))
        or not re.search(r"完成|做完|做了|练完", text)
    ):
        return {"status": "needs_clarification", "reply": "尚未记录，请确认完成的训练动作。"}
    counts = re.findall(r"([-+]?\d+(?:\.\d+)?|[一二三四五六七八九十]+)\s*组", text)
    chinese = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if len(counts) != 1:
        return {"status": "needs_clarification", "reply": "尚未记录，请明确完成了几组。"}
    sets = chinese.get(counts[0], int(counts[0]) if counts[0].isdigit() else None)
    if sets is None or not 1 <= sets <= 30:
        return {"status": "needs_clarification", "reply": "尚未记录，请给出1至30的整数训练组数。"}
    if "昨晚" in text or "昨天" in text:
        tz = timezone(timedelta(hours=8))
        session_day = datetime.now(tz).date() - timedelta(days=1)
        performed_at = datetime.combine(session_day, time.min, tzinfo=tz)
        time_note = "用户称昨天，具体时刻未提供；时间戳仅为日期锚点。"
    elif "今天" in text or "刚" in text:
        performed_at = datetime.now(timezone.utc)
        time_note = None
    else:
        return {
            "status": "needs_clarification",
            "reply": "尚未记录，请说明训练发生在今天还是昨天。",
        }
    return {
        "status": "ready",
        "fields": {
            "performed_at": performed_at,
            "workout_name": f"{exercises[0]}训练",
            "exercises": [{"name": exercises[0], "sets": sets}],
            "notes": time_note,
        },
        "exercise_name": exercises[0],
        "completed_sets": sets,
    }


def merge_workout_followup(original: str, missing: list[str], answer: str) -> str | None:
    """Accept only an unambiguous value for a field explicitly requested last turn."""
    text = answer.strip().rstrip("。.!！")
    if any("时长" in item for item in missing):
        duration = re.fullmatch(r"(?:训练了|练了)?\s*([-+]?\d+(?:\.\d+)?)\s*分钟", text)
        if duration:
            value = duration[1]
            if not value.isdigit() or not 1 <= int(value) <= 1440:
                return original  # Keep the task pending; never turn malformed values into facts.
            previous = re.findall(r"[-+]?\d+(?:\.\d+)?\s*分钟", original)
            if len(previous) == 1:
                return original.replace(previous[0], value + "分钟", 1)
            if len(previous) > 1:
                return original  # Ambiguous original durations need a restated complete request.
            return original + "，" + text
    if any("主观强度" in item for item in missing):
        intensity = re.fullmatch(r"(?:主观强度|RPE)\s*([-+]?\d+(?:\.\d+)?)\s*分?", text, re.I)
        if intensity:
            value = intensity[1]
            if not value.isdigit() or not 1 <= int(value) <= 10:
                return original
            return re.sub(
                r"(?:主观强度|RPE)\s*[-+]?\d+(?:\.\d+)?\s*分?",
                "主观强度" + value + "分",
                original,
                count=1,
                flags=re.I,
            )
    patterns = {
        "类型": r"(?:哑铃训练|力量训练|跑步|骑行|游泳)",
        "时间": r"(?:今天|刚完成|我刚完成)",
        "本人": r"(?:我刚完成|我今天完成)",
    }
    for field, pattern in patterns.items():
        if any(field in item for item in missing) and re.fullmatch(pattern, text, re.I):
            return original + "，" + text
    return None


def _minute_count(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    digits = {character: value for value, character in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if token in digits:
        return digits[token]
    if re.fullmatch(r"[二三四五六七八九]?十[一二三四五六七八九]?", token):
        tens, units = token.split("十")
        return digits.get(tens, 1) * 10 + digits.get(units, 0)
    return None


def parse_workout_record(message: str, *, now: datetime | None = None) -> dict:
    if not RECORD_REQUEST.search(message):
        return {"status": "not_requested"}
    message = record_task_text(message)
    if re.search(r"不要|别|不用|朋友|他|她|如果|假如|打算|准备", message):
        return {"status": "blocked", "reply": "这条消息的记录归属或授权不明确，未写入训练记录。"}
    missing = []
    if not re.search(r"我(?:今天|昨天|昨晚|刚刚|刚|已经|刚才)?(?:完成|做完|练完|跑完)", message):
        missing.append("本人已完成训练的确认")
    yesterday = any(word in message for word in ("昨天", "昨晚"))
    today = "今天" in message or (not yesterday and "刚" in message)
    unsupported_date = re.search(r"前天|明天|后天|上周|下周|上个月|20\d{2}[年/-]", message)
    if not (yesterday or today) or (yesterday and today) or unsupported_date:
        missing.append("一种明确的训练时间（当前支持今天或昨天）")
    activities = [
        word for word in ("哑铃训练", "力量训练", "跑步", "骑行", "游泳", "跳绳") if word in message
    ]
    if len(activities) != 1:
        missing.append("一种明确的训练类型")
    durations = re.findall(r"([-+]?\d+(?:\.\d+)?|[零一二三四五六七八九十两]+)\s*分钟", message)
    duration = _minute_count(durations[0]) if len(durations) == 1 else None
    if duration is None or not 1 <= duration <= 1440:
        missing.append("有效的训练时长（分钟）")
    intensity = re.search(r"(?:主观强度|RPE)\s*([-+]?\d+(?:\.\d+)?)\s*分?", message, re.I)
    if intensity and (not intensity[1].isdigit() or not 1 <= int(intensity[1]) <= 10):
        missing.append("1至10的主观强度")
    if missing:
        return {
            "status": "needs_clarification",
            "missing": missing,
            "reply": "尚未记录，请补充：" + "、".join(missing) + "。",
        }
    fields = {"workout_name": activities[0], "duration_minutes": duration}
    if yesterday:
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            raise ValueError("Date reference must include a timezone")
        tz = timezone(timedelta(hours=8))
        session_day = reference.astimezone(tz).date() - timedelta(days=1)
        fields["performed_at"] = datetime.combine(session_day, time.min, tzinfo=tz)
        fields["notes"] = "用户称昨天，具体时刻未提供；时间戳仅为日期锚点。"
    if intensity:
        fields["rpe"] = int(intensity[1])
    return {"status": "ready", "fields": fields}
