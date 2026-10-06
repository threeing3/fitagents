"""Shared transactional boundary for plan producers; no commits or blind retries."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.agent_runtime import ToolPreconditionError


class StalePlanWriteError(ToolPreconditionError):
    """The expected active plan is no longer the current write target."""


def lock_plan_owner(db: Session, user_id: uuid.UUID) -> models.User:
    """Serialize domain writers without blocking independent FK-only inserts.

    PostgreSQL NO KEY UPDATE conflicts with other domain writers, but permits
    the KEY SHARE lock needed by durable quota reservations in another session.
    This gate never changes a user primary key. SQLite ignores row-lock syntax.
    """
    with db.no_autoflush:
        user = db.scalar(
            select(models.User).where(models.User.id == user_id).with_for_update(key_share=True)
        )
    if user is None:
        raise ValueError("Plan owner not found")
    return user


def replace_plan_content(
    db: Session,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    expected: dict[str, Any],
    candidate: dict[str, Any],
) -> models.TrainingPlan:
    """Atomic expected-content comparison, including owner and active status.

    JSON equality uses the database's existing JSON type. SQLite serialization
    can conservatively reject equivalent objects with different key order.
    No ORM assignment is made before the conditional database write.
    """
    lock_plan_owner(db, user_id)
    with db.no_autoflush:
        result = db.execute(
            update(models.TrainingPlan)
            .where(
                models.TrainingPlan.id == plan_id,
                models.TrainingPlan.user_id == user_id,
                models.TrainingPlan.status == "active",
                models.TrainingPlan.plan_json == expected,
            )
            .values(plan_json=copy.deepcopy(candidate))
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise StalePlanWriteError("Plan changed before persistence; read and verify again")
        plan = db.get(models.TrainingPlan, plan_id, populate_existing=True)
    if plan is None:
        raise StalePlanWriteError("Updated plan is no longer available")
    return plan
