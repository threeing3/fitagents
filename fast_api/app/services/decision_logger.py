import uuid
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.db import models


class DecisionLogger:
    """Persist and retrieve explainable agent decisions."""

    def __init__(self, db: Session):
        self.db = db

    def log_decision(
        self,
        user_id: uuid.UUID,
        decision: dict[str, Any],
    ) -> models.AgentDecision:
        item = models.AgentDecision(
            user_id=user_id,
            decision_type=decision["decision_type"],
            input_summary=decision.get("input_summary", ""),
            context_used=decision.get("context_used", {}),
            decision_result=decision["decision_result"],
            reason=decision["reason"],
            confidence_score=float(decision.get("confidence_score", 0.75)),
            accepted_by_user=decision.get("accepted_by_user"),
        )
        self.db.add(item)
        self.db.flush()
        return item

    def get_recent_decisions(
        self,
        user_id: uuid.UUID,
        decision_type: str | None = None,
        limit: int = 10,
    ) -> list[models.AgentDecision]:
        filters = [models.AgentDecision.user_id == user_id]
        if decision_type:
            filters.append(models.AgentDecision.decision_type == decision_type)
        return list(
            self.db.scalars(
                select(models.AgentDecision)
                .where(*filters)
                .order_by(desc(models.AgentDecision.created_at))
                .limit(limit)
            )
        )
