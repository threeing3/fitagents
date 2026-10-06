"""Explicit bounded chat commands, not a general natural-language authority parser."""

import re
import uuid

from sqlalchemy.orm import Session

from fast_api.app.services.approval_manager import ApprovalManager
from fast_api.app.services.responsibilities import ResponsibilityService

CREATE = re.compile(
    r"(?:请)?(?:帮我)?创建(?P<weeks>\d{1,2})周每周(?P<day>[一二三四五六日天])"
    r"(?P<hour>\d{1,2})[:：](?P<minute>\d{2})训练复盘(?:[，,]调整(?:计划)?先问我)?[。！!]?"
)
GUIDE = "请明确写：创建4周每周日18:00训练复盘，调整先问我。时间采用你的账户时区。"


def handle_command(db: Session, user_id: uuid.UUID, message: str):
    text = message.strip()
    service = ResponsibilityService(db)
    match = CREATE.fullmatch(text)
    if match:
        try:
            task = service.create_weekly(
                user_id,
                text,
                weeks=int(match["weeks"]),
                weekday="一二三四五六日".index(match["day"].replace("天", "日")),
                hour=int(match["hour"]),
                minute=int(match["minute"]),
            )
        except ValueError as exc:
            return f"未创建责任：{exc}。{GUIDE}", {"responsibility_status": "clarify"}
        snapshot = service.snapshot(task)
        return (
            f"已创建{match['weeks']}周训练复盘，账户时区为{snapshot['configuration']['timezone']}。"
            "复盘只生成记录和待审批草案，不自动修改计划。"
            f"责任编号：{task.id}。",
            {"responsibility": snapshot},
        )
    if re.match(r"^(?:请)?(?:帮我)?创建.*(?:复盘|责任)", text):
        return GUIDE, {"responsibility_status": "clarify"}
    if text == "查看训练复盘责任":
        tasks = service.list_for_user(user_id)
        details = [
            f"{item['id']}：{item['status']}，下一次复盘 {item['configuration']['next_wake_at']}。"
            for item in tasks[:10]
        ]
        return f"当前有{len(tasks)}项训练复盘责任。\n" + "\n".join(details), {
            "responsibilities": tasks
        }
    match = re.fullmatch(r"(暂停|恢复|取消)训练复盘\s+([0-9a-fA-F-]{36})", text)
    if match:
        try:
            task = service.transition(
                uuid.UUID(match[2]),
                user_id,
                {"暂停": "pause", "恢复": "resume", "取消": "cancel"}[match[1]],
            )
        except ValueError as exc:
            return f"未变更责任：{exc}。", {"responsibility_status": "rejected"}
        return f"责任已{match[1]}。", {"responsibility": service.snapshot(task)}
    if text == "查看待审批调整":
        pending = [item.to_dict() for item in ApprovalManager(db).get_pending(user_id)]
        details = []
        for item in pending:
            payload = item["input_summary"]
            if item["tool_name"] == "plan.reduce_sets":
                summary = (
                    f"计划 {payload.get('plan_id')}，日期 {payload.get('day_date')}；"
                    f"该日每个动作减少 {payload.get('reduce_by')} 组，其他日期和饮食不变；"
                    f"理由：{payload.get('reason')}"
                )
            else:
                summary = item["tool_description"]
            details.append(
                f"编号 {item['approval_id']}；{summary}；有效期至 {item['expires_at']}。"
            )
        return (
            f"当前有{len(pending)}项待审批动作。\n"
            + "\n".join(details)
            + "\n减量草案请明确回复：批准调整 编号，或拒绝调整 编号。",
            {"pending_approvals": pending},
        )
    match = re.fullmatch(r"(批准|拒绝)调整\s+([0-9a-fA-F-]{36})", text)
    if match:
        manager = ApprovalManager(db)
        item = manager.get(match[2])
        if item is None or item.user_id != user_id or item.tool_name != "plan.reduce_sets":
            return "未处理：找不到属于你的减量草案。", {"approval_status": "rejected"}
        if item.status != "pending":
            return f"未重复处理：审批状态为{item.status}。", {"approval_status": item.status}
        decision = manager.approve(match[2]) if match[1] == "批准" else manager.deny(match[2])
        if decision is None:
            return "未处理：审批已变化或过期，请重新查看。", {"approval_status": "rejected"}
        return (
            "已批准这一次调整，等待后台执行；尚未确认计划修改成功。"
            if match[1] == "批准"
            else "已拒绝这一次调整，计划不变。",
            {"approval": decision.to_dict()},
        )
    return None
