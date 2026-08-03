"""Short-term follow-up state, inspired by coding-agent task continuity."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from fast_api.app.db import models


@dataclass
class FollowupResolution:
    """Resolved answer to a pending assistant question."""

    resolved: bool
    normalized_message: str
    pending_question_id: uuid.UUID | None = None
    question_type: str | None = None
    selected_option: dict[str, Any] | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "normalized_message": self.normalized_message,
            "pending_question_id": str(self.pending_question_id) if self.pending_question_id else None,
            "question_type": self.question_type,
            "selected_option": self.selected_option,
            "reason": self.reason,
        }


class FollowupResolver:
    """Persist and resolve short answers against the previous assistant turn."""

    DEFAULT_TTL_MINUTES = 45

    OPTION_PATTERN = re.compile(
        r"(?:^|\n|\s)([A-Da-d])[\.\)、:：\-]\s*([^A-D\n]{1,120})"
    )
    YES_VALUES = {"是", "对", "可以", "好", "要", "需要", "继续", "yes", "y", "ok", "okay"}
    NO_VALUES = {"不", "不是", "不要", "不用", "不需要", "先不", "no", "n"}
    ORDINAL_VALUES = {
        "1": "A",
        "一": "A",
        "第一个": "A",
        "2": "B",
        "二": "B",
        "第二个": "B",
        "3": "C",
        "三": "C",
        "第三个": "C",
        "4": "D",
        "四": "D",
        "第四个": "D",
    }

    def __init__(self, db: Session):
        self.db = db

    def resolve(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        message: str,
        resolved_message_id: uuid.UUID | None = None,
    ) -> FollowupResolution:
        pending = self.get_active_question(user_id, session_id)
        if pending is None:
            return FollowupResolution(False, message, reason="no_pending_question")

        option = self._match_option(message, pending.options_json or [])
        if option is None:
            return FollowupResolution(False, message, pending.id, pending.question_type, reason="no_option_match")

        normalized = self._normalized_message(pending, option, message)
        pending.status = "answered"
        pending.answer_json = {
            "raw_message": message,
            "selected_option": option,
            "normalized_message": normalized,
            "resolved_at": datetime.utcnow().isoformat(),
        }
        pending.resolved_message_id = resolved_message_id
        pending.updated_at = datetime.utcnow()
        self.db.flush()
        return FollowupResolution(
            True,
            normalized,
            pending.id,
            pending.question_type,
            option,
            "matched_pending_question",
        )

    def get_active_question(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> models.PendingQuestion | None:
        now = datetime.utcnow()
        return self.db.scalar(
            select(models.PendingQuestion)
            .where(
                models.PendingQuestion.user_id == user_id,
                models.PendingQuestion.session_id == session_id,
                models.PendingQuestion.status == "pending",
                or_(
                    models.PendingQuestion.expires_at.is_(None),
                    models.PendingQuestion.expires_at >= now,
                ),
            )
            .order_by(desc(models.PendingQuestion.created_at))
            .limit(1)
        )

    def remember_from_assistant_message(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        assistant_text: str,
    ) -> models.PendingQuestion | None:
        options = self.extract_options(assistant_text)
        question_type = self._infer_question_type(assistant_text, options)
        if not options and question_type != "yes_no":
            return None

        self.expire_open_questions(user_id, session_id)
        question = models.PendingQuestion(
            user_id=user_id,
            session_id=session_id,
            assistant_message_id=assistant_message_id,
            question_type=question_type,
            prompt_text=assistant_text[-1200:],
            options_json=options or [
                {"key": "yes", "label": "yes", "meaning": "用户确认"},
                {"key": "no", "label": "no", "meaning": "用户否定"},
            ],
            status="pending",
            answer_json={},
            expires_at=datetime.utcnow() + timedelta(minutes=self.DEFAULT_TTL_MINUTES),
        )
        self.db.add(question)
        self.db.flush()
        return question

    def expire_open_questions(self, user_id: uuid.UUID, session_id: uuid.UUID) -> int:
        questions = list(
            self.db.scalars(
                select(models.PendingQuestion).where(
                    models.PendingQuestion.user_id == user_id,
                    models.PendingQuestion.session_id == session_id,
                    models.PendingQuestion.status == "pending",
                )
            )
        )
        for question in questions:
            question.status = "expired"
            question.updated_at = datetime.utcnow()
        if questions:
            self.db.flush()
        return len(questions)

    def extract_options(self, text: str) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        for match in self.OPTION_PATTERN.finditer(text):
            key = match.group(1).upper()
            label = self._clean_label(match.group(2))
            if not label:
                continue
            options.append({"key": key, "label": label, "meaning": label})
        seen: set[str] = set()
        deduped = []
        for option in options:
            if option["key"] in seen:
                continue
            seen.add(option["key"])
            deduped.append(option)
        return deduped

    def _match_option(self, message: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
        normalized = message.strip().lower()
        compact = re.sub(r"[\s。.!！,，、]+", "", normalized)
        if compact in self.ORDINAL_VALUES:
            compact = self.ORDINAL_VALUES[compact].lower()
        if compact in self.YES_VALUES:
            compact = "yes"
        elif compact in self.NO_VALUES:
            compact = "no"

        for option in options:
            key = str(option.get("key") or "").strip().lower()
            label = str(option.get("label") or "").strip().lower()
            if compact == key or compact == label:
                return option
            if len(compact) > 1 and label and compact in label:
                return option
        return None

    def _normalized_message(
        self,
        pending: models.PendingQuestion,
        option: dict[str, Any],
        raw_message: str,
    ) -> str:
        key = option.get("key")
        meaning = option.get("meaning") or option.get("label") or key
        return (
            f"用户正在回答上一轮追问。上一轮问题类型：{pending.question_type}。"
            f"用户原始回答：{raw_message}。用户选择：{key} = {meaning}。"
            "请基于这个已解析的回答继续上一轮任务，不要把它当成新的寒暄。"
        )

    def _infer_question_type(self, text: str, options: list[dict[str, Any]]) -> str:
        lowered = text.lower()
        if any(term in lowered for term in ["疼", "痛", "酸", "pain", "soreness"]):
            return "pain_type_selection"
        if options:
            return "option_selection"
        return "yes_no"

    def _clean_label(self, label: str) -> str:
        label = label.strip()
        label = re.split(r"\s{2,}|\n", label)[0]
        return label.strip(" -:：；;，,。.)）")
