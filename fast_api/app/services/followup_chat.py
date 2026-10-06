"""Explicit chat refusal for a bounded evaluation, never a global notification policy."""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.decision_evaluation import DecisionEvaluationService
from fast_api.app.services.plan_writes import lock_plan_owner

REFUSALS = {
    "这件事别再问了",
    "这件事不要再问了",
    "请不要再问这件事",
    "不用再追问这件事",
    "不再追问这件事",
    "这条跟进我不想回答",
    "我不想回答这个问题",
    "别再问了",
}
EXPLICIT = re.compile(r"(?:请)?停止跟进\s+([0-9a-fA-F-]{36})")
GLOBAL = {"以后别再追问我", "以后不要再追问我", "以后别再问我", "停止所有追问"}


def handle_followup_command(db: Session, user_id: uuid.UUID, message: str):
    text = message.strip().rstrip("。！!")
    match = EXPLICIT.fullmatch(text)
    if text not in REFUSALS and text not in GLOBAL and match is None:
        return None
    lock_plan_owner(db, user_id)
    if text in GLOBAL:
        return (
            "这句话涉及所有后续事项，我没有将它扩大为永久关闭提醒。请指定当前跟进：停止跟进 编号。",
            {"followup_status": "clarify"},
        )
    if match:
        try:
            followup_id = uuid.UUID(match[1])
        except ValueError:
            return "跟进编号无效，未修改任何跟进。", {"followup_status": "rejected"}
    else:
        pending = db.scalars(
            select(models.DecisionFollowup)
            .where(
                models.DecisionFollowup.user_id == user_id,
                models.DecisionFollowup.status == "pending",
            )
            .order_by(models.DecisionFollowup.scheduled_at)
            .execution_options(populate_existing=True)
        ).all()
        scopes = {item.evaluation_plan_id for item in pending}
        if not scopes:
            return "没有当前待跟进的问题；未关闭未来其他事项的提醒。", {
                "followup_status": "none_pending"
            }
        if len(scopes) > 1:
            choices = "；".join(str(item.id) for item in pending[:5])
            return (
                f"有多项待跟进，无法确定你指哪件事。请写“停止跟进 编号”。当前编号：{choices}",
                {"followup_status": "clarify"},
            )
        followup_id = pending[0].id
    try:
        result = DecisionEvaluationService(db).decline_followup(followup_id, user_id)
    except ValueError:
        return "未找到属于你且可停止的跟进；未修改其他事项。", {"followup_status": "rejected"}
    return (
        "已停止这项跟进的追问，历史记录和风险提示保留；不会将未回答记为训练失败。",
        {"followup_status": "declined", "followup": result},
    )
