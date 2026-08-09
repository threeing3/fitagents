"""Idempotent, non-destructive daily demo account rotation."""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings, get_settings
from fast_api.app.core.security import hash_password
from fast_api.app.db import models


class DemoAccountService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    def ensure_today(self) -> models.User | None:
        if not self.settings.demo_mode:
            return None
        if not self.settings.demo_email or not self.settings.demo_password:
            raise RuntimeError("Demo account secrets are incomplete")

        today = date.today()
        state = self.db.scalar(
            select(models.DemoResetState).where(models.DemoResetState.demo_key == "public-demo")
        )
        if state is not None and state.reset_date == today:
            user = self.db.get(models.User, state.active_user_id)
            if user is not None:
                return user

        if state is not None:
            previous = self.db.get(models.User, state.active_user_id)
            if previous is not None:
                archive_id = uuid.uuid4().hex[:12]
                previous.email = f"archived-demo-{archive_id}@invalid.local"
                previous.username = f"archived-demo-{archive_id}"
                self.db.flush()

        email = self.settings.demo_email.strip().lower()
        existing = self.db.scalar(select(models.User).where(models.User.email == email))
        if existing is not None:
            user = existing
            user.password_hash = hash_password(self.settings.demo_password)
        else:
            user = models.User(
                email=email,
                username=f"public-demo-{uuid.uuid4().hex[:8]}",
                password_hash=hash_password(self.settings.demo_password),
                display_name="公开演示用户",
            )
            self.db.add(user)
            self.db.flush()

        if self.db.get(models.UserProfile, user.id) is None:
            self.db.add(models.UserProfile(user_id=user.id))

        if state is None:
            state = models.DemoResetState(
                demo_key="public-demo",
                active_user_id=user.id,
                reset_date=today,
                reset_count=1,
            )
            self.db.add(state)
        else:
            state.active_user_id = user.id
            state.reset_date = today
            state.reset_count += 1

        self.db.commit()
        self.db.refresh(user)
        return user

    def active_user(self) -> models.User | None:
        return self.ensure_today()
