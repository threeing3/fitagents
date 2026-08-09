"""Persistent model-call quota enforcement and public-safe snapshots."""

import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from fast_api.app.core.config import Settings, get_settings
from fast_api.app.db import models
from fast_api.app.db.database import SessionLocal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuotaSnapshot:
    event_date: str
    user_used: int
    user_limit: int
    global_used: int
    global_limit: int
    live_calls_available: bool
    fallback_mode: str = "deterministic_offline"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class UsageQuotaService:
    """Reserve one durable event before constructing a billed model client."""

    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session_factory = session_factory or SessionLocal

    @staticmethod
    def _count(db: Session, today: date, user_id: uuid.UUID | None = None) -> int:
        query = select(func.count(models.UsageEvent.id)).where(
            models.UsageEvent.event_date == today,
            models.UsageEvent.event_type == "model_call",
            models.UsageEvent.status == "reserved",
        )
        if user_id is not None:
            query = query.where(models.UsageEvent.user_id == user_id)
        return int(db.scalar(query) or 0)

    def _snapshot(self, db: Session, user_id: uuid.UUID | None, today: date) -> QuotaSnapshot:
        global_used = self._count(db, today)
        user_used = self._count(db, today, user_id) if user_id is not None else 0
        user_available = user_id is None or user_used < self.settings.daily_model_call_limit
        global_available = global_used < self.settings.global_daily_model_limit
        return QuotaSnapshot(
            event_date=today.isoformat(),
            user_used=user_used,
            user_limit=self.settings.daily_model_call_limit,
            global_used=global_used,
            global_limit=self.settings.global_daily_model_limit,
            live_calls_available=user_available and global_available,
        )

    def snapshot(self, user_id: uuid.UUID | None) -> QuotaSnapshot:
        today = date.today()
        try:
            with self.session_factory() as db:
                return self._snapshot(db, user_id, today)
        except Exception:
            if self.settings.is_production:
                raise
            logger.warning("Usage quota store unavailable; allowing local development call")
            return QuotaSnapshot(
                event_date=today.isoformat(),
                user_used=0,
                user_limit=self.settings.daily_model_call_limit,
                global_used=0,
                global_limit=self.settings.global_daily_model_limit,
                live_calls_available=True,
            )

    def reserve(
        self,
        user_id: uuid.UUID | None,
        *,
        provider: str,
        model_name: str,
        endpoint: str | None,
    ) -> bool:
        today = date.today()
        try:
            with self.session_factory() as db, db.begin():
                if db.bind is not None and db.bind.dialect.name == "postgresql":
                    # Serialize reservations for the UTC-independent business day.
                    db.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:quota_key))"),
                        {"quota_key": f"model-quota:{today.isoformat()}"},
                    )
                snapshot = self._snapshot(db, user_id, today)
                if not snapshot.live_calls_available:
                    return False
                db.add(
                    models.UsageEvent(
                        user_id=user_id,
                        event_date=today,
                        event_type="model_call",
                        provider=provider,
                        model_name=model_name,
                        endpoint=endpoint,
                        status="reserved",
                    )
                )
            return True
        except Exception:
            if self.settings.is_production:
                logger.exception("Usage quota reservation failed closed")
                return False
            logger.warning("Usage quota store unavailable; allowing local development call")
            return True
