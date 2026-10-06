"""Explicit owned workout correction proposal, then a session-bound confirmation."""

import re
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import select

from fast_api.app.core.errors import IdempotencyConflictError, ResourceNotFoundError
from fast_api.app.db import models
from fast_api.app.schemas.agent import WorkoutCorrectionRequest
from fast_api.app.services.plan_writes import lock_plan_owner
from fast_api.app.services.workout_corrections import correct_workout, list_workouts

ID = r"[0-9a-fA-F-]{36}"
PROPOSE = re.compile(rf"更正训练记录\s+({ID})\s+(.+?)[；;]\s*原因[：:]\s*(.+)", re.S)
CONFIRM = re.compile(rf"(确认|取消)更正训练\s+({ID})")
FIELDS = {
    "时长": ("duration_minutes", r"(\d+)分钟"),
    "强度": ("rpe", r"(\d+)"),
    "完成度": ("completion_rate", r"(\d+(?:\.\d+)?)%"),
}


def facts_text(values):
    labels = {"duration_minutes": "时长（分钟）", "rpe": "整体强度", "completion_rate": "完成度"}
    return "；".join(
        f"{labels[key]}："
        + (
            "未记录"
            if value is None
            else f"{value * 100:g}%"
            if key == "completion_rate"
            else str(value)
        )
        for key, value in values.items()
    )


def handle_workout_correction_command(service, user_id, session_id, message):
    text = message.strip().rstrip("。！!")
    confirmation = CONFIRM.fullmatch(text)
    proposal = PROPOSE.fullmatch(text)
    looks_like_correction = bool(
        re.search(
            r"(?:更正|纠正|修改).*?训练记录|训练记录.*?(?:写错|记错|不对)|更正训练", text, re.S
        )
    )
    if confirmation is None and proposal is None and not looks_like_correction:
        if text in {"好", "确认", "确认更正", "取消更正"}:
            pending = service.db.scalar(
                select(models.PendingQuestion).where(
                    models.PendingQuestion.user_id == user_id,
                    models.PendingQuestion.session_id == session_id,
                    models.PendingQuestion.question_type == "workout_correction",
                    models.PendingQuestion.status == "pending",
                )
            )
            if pending is not None:
                return (
                    "尚未更正训练。请核对原值和新值，并使用完整的“确认更正训练 确认编号”；不能用模糊回答代替授权。",
                    {"workout_correction_status": "clarify"},
                )
        return None
    db = service.db
    lock_plan_owner(db, user_id)
    if confirmation:
        try:
            question_id = uuid.UUID(confirmation[2])
        except ValueError:
            return "确认编号无效，未修改训练记录。", {"workout_correction_status": "rejected"}
        pending = db.scalar(
            select(models.PendingQuestion)
            .where(
                models.PendingQuestion.id == question_id,
                models.PendingQuestion.user_id == user_id,
                models.PendingQuestion.session_id == session_id,
                models.PendingQuestion.question_type == "workout_correction",
            )
            .execution_options(populate_existing=True)
        )
        if pending is None or pending.status not in {"pending", "answered"}:
            return "未找到当前会话中可确认的更正，未修改训练记录。", {
                "workout_correction_status": "rejected"
            }
        if pending.status == "pending":
            expiry = pending.expires_at
            if expiry is None or expiry.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
                pending.status = "expired"
                return "更正确认已过期，请重新核对记录。", {"workout_correction_status": "rejected"}
        if confirmation[1] == "取消":
            if pending.status == "answered":
                return "该更正已经执行，不能把取消当作回滚；如需修改，请重新核对。", {
                    "workout_correction_status": "rejected"
                }
            pending.status = "cancelled"
            return "已取消这项更正，训练记录未修改。", {"workout_correction_status": "cancelled"}
        request = WorkoutCorrectionRequest.model_validate(pending.answer_json["request"])
        log_id = uuid.UUID(pending.answer_json["workout_log_id"])
        # Same commit as canonical correction: a saved fact cannot leave a pending approval.
        pending.status = "answered"
        try:
            result = correct_workout(service, user_id, log_id, request)
        except (IdempotencyConflictError, ResourceNotFoundError):
            pending = db.get(models.PendingQuestion, question_id, populate_existing=True)
            pending.status = "expired"
            return "记录或版本已变化，这项更正未执行。请重新核对当前值。", {
                "workout_correction_status": "rejected"
            }
        return (
            f"更正已确认：{facts_text(result['before'])} → {facts_text(result['after'])}。"
            f"版本 {result['revision']}，审计编号 {result['audit_id']}。"
            + ("恢复原结果，未重复更正。" if result["idempotent_replay"] else ""),
            {"workout_correction_status": "corrected", "correction": result},
        )
    if proposal is None:
        rows = list_workouts(db, user_id, limit=5)
        choices = "；".join(
            f"{row['workout_name']}（{row['performed_at']}，编号 {row['id']}）" for row in rows
        )
        return (
            "这是更正旧记录，不会新增训练。请在训练记录页面选择原记录，或发送："
            "更正训练记录 编号 时长为20分钟；原因：核对手表。"
            "也支持强度为4、完成度为80%，可用逗号分隔。随后还需核对并确认。"
            + (f"你的最近记录：{choices}" if choices else "暂无可核对记录。"),
            {"workout_correction_status": "clarify"},
        )
    try:
        log_id = uuid.UUID(proposal[1])
        changes = {}
        for clause in re.split(r"[，,]", proposal[2]):
            matched = False
            for label, (field, pattern) in FIELDS.items():
                match = re.fullmatch(label + "为" + pattern, clause.strip())
                if match:
                    if field in changes:
                        raise ValueError("duplicate field")
                    changes[field] = (
                        float(match[1]) / 100 if field == "completion_rate" else int(match[1])
                    )
                    matched = True
                    break
            if not matched:
                raise ValueError("unsupported fact")
        rows = list_workouts(db, user_id, limit=1, log_id=log_id)
        if not rows or not rows[0]["correction_available"]:
            raise ValueError("unverified source")
        row = rows[0]
        question_id = uuid.uuid4()
        request = WorkoutCorrectionRequest(
            idempotency_key=f"chat-correction:{question_id}",
            expected_revision=row["revision"],
            expected={key: row[key] for key in changes},
            changes=changes,
            reason=proposal[3],
        )
        if request.expected == request.changes:
            raise ValueError("no changed fact")
    except (ValueError, ValidationError):
        return "未能核对唯一原记录及有效新值，未修改或新增训练；请在训练记录页面核对。", {
            "workout_correction_status": "rejected"
        }
    db.add(
        models.PendingQuestion(
            id=question_id,
            user_id=user_id,
            session_id=session_id,
            question_type="workout_correction",
            prompt_text=text,
            status="pending",
            answer_json={
                "workout_log_id": str(log_id),
                "request": request.model_dump(mode="json", exclude_unset=True),
            },
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=45),
        )
    )
    db.flush()
    return (
        f"尚未修改训练。目标：{row['workout_name']}，日期 {row['performed_at']}，记录编号 {log_id}，版本 {row['revision']}。"
        f"\n{facts_text(request.expected.model_dump(exclude_unset=True))} → {facts_text(changes)}。原因：{request.reason}。"
        f"\n核对后发送：确认更正训练 {question_id}；或取消更正训练 {question_id}。45分钟内有效。",
        {"workout_correction_status": "awaiting_confirmation", "confirmation_id": str(question_id)},
    )
