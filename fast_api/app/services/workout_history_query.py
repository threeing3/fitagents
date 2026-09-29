"""Explicit self-history queries, executed before the current turn's workout write."""

import re

from sqlalchemy import select

from fast_api.app.db import models


def history_query(message: str) -> dict | None:
    # Scope activity and negation to the query clause, not a neighbouring write.
    clauses = re.split(r"[，,。！？!?；;]|再(?:帮我)?", message)
    for index, clause in enumerate(clauses):
        if not re.search(r"上次|上一次|最近一次", clause):
            continue
        if not re.search(
            r"查|看看|看一下|告诉我|多久|多少|什么|哪天|何时|几分钟|几组|记得",
            clause,
        ):
            continue
        if re.search(r"不要|不用|别|朋友|他|她", clause):
            continue
        exercises = [name for name in ("深蹲", "卧推", "硬拉") if name in clause]
        if not exercises and re.search(r"几组|多少组", clause):
            # An elliptical follow-up may refer to one exercise in an earlier clause.
            preceding = "，".join(clauses[:index])
            exercises = [name for name in ("深蹲", "卧推", "硬拉") if name in preceding]
        if exercises and re.search(r"几组|多少组", clause):
            return {"kind": "exercise_sets", "exercises": exercises}
        if exercises and re.search(r"多少公斤|几公斤|多重|重量", clause):
            return {"kind": "exercise_weight", "exercises": exercises}
        activities = [
            activity
            for activity in ("哑铃训练", "力量训练", "跑步", "骑行", "游泳")
            if activity in clause
        ]
        if not activities and "跑了" in clause:
            activities = ["跑步"]
        if not activities and not re.search(r"训练|运动|锻炼", clause):
            continue
        return {"activities": activities}
    return None


def read_workout_history(db, user_id, query: dict) -> dict:
    if query.get("kind") in {"exercise_sets", "exercise_weight"}:
        exercises = query["exercises"]
        if len(exercises) != 1:
            return {"status": "needs_clarification", "reply": "你想查哪一个动作的上次记录？"}
        exercise_name = exercises[0]
        session = db.scalar(
            select(models.WorkoutSession)
            .join(models.ExerciseLog, models.ExerciseLog.session_id == models.WorkoutSession.id)
            .where(
                models.WorkoutSession.user_id == user_id,
                models.ExerciseLog.user_id == user_id,
                models.ExerciseLog.exercise_name == exercise_name,
            )
            .order_by(
                models.WorkoutSession.session_date.desc(),
                models.WorkoutSession.created_at.desc(),
                models.WorkoutSession.id.desc(),
            )
            .limit(1)
        )
        if session is None:
            return {
                "status": "not_found",
                "reply": f"没有查到你此前的{exercise_name}记录。",
            }
        completed = db.scalars(
            select(models.ExerciseLog).where(
                models.ExerciseLog.user_id == user_id,
                models.ExerciseLog.session_id == session.id,
                models.ExerciseLog.exercise_name == exercise_name,
                models.ExerciseLog.completed.is_(True),
            )
        ).all()
        if query["kind"] == "exercise_weight":
            weights = sorted({row.weight for row in completed if row.weight is not None})
            if not weights:
                return {
                    "status": "not_found",
                    "reply": f"找到上次{exercise_name}场次，但没有确认完成的重量记录。",
                }
            listed = "、".join(f"{weight:g}" for weight in weights)
            return {
                "status": "found",
                "session_id": str(session.id),
                "exercise_name": exercise_name,
                "weights_kg": weights,
                "session_date": session.session_date.isoformat(),
                "reply": f"此前最近一次{exercise_name}记录：{session.session_date:%Y-%m-%d}，完成组的重量为{listed}公斤。",
            }
        set_count = len({row.set_index for row in completed})
        if not set_count:
            return {
                "status": "not_found",
                "reply": f"找到上次{exercise_name}场次，但没有确认完成的组数。",
            }
        return {
            "status": "found",
            "session_id": str(session.id),
            "exercise_name": exercise_name,
            "completed_sets": set_count,
            "session_date": session.session_date.isoformat(),
            "reply": f"此前最近一次{exercise_name}记录：{session.session_date:%Y-%m-%d}，完成{set_count}组。",
        }
    activities = query["activities"]
    if len(activities) > 1:
        return {"status": "needs_clarification", "reply": "你想查哪一种运动的上次记录？"}
    statement = select(models.WorkoutLog).where(models.WorkoutLog.user_id == user_id)
    if activities:
        statement = statement.where(models.WorkoutLog.workout_name == activities[0])
    row = db.scalar(
        statement.order_by(
            models.WorkoutLog.performed_at.desc(),
            models.WorkoutLog.created_at.desc(),
            models.WorkoutLog.id.desc(),
        ).limit(1)
    )
    if row is None:
        return {"status": "not_found", "reply": "没有查到你此前的相关训练记录，无法确认上次时长。"}
    return {
        "status": "found",
        "record_id": str(row.id),
        "workout_name": row.workout_name,
        "duration_minutes": row.duration_minutes,
        "performed_at": row.performed_at.isoformat(),
        "reply": (
            f"此前最近一次{row.workout_name}记录：{row.performed_at:%Y-%m-%d}，"
            + (
                f"{row.duration_minutes}分钟。"
                if row.duration_minutes is not None
                else "未记录时长。"
            )
        ),
    }
