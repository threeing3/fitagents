"""Persistent quota and non-destructive demo account tests."""

import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.demo_accounts import DemoAccountService
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.usage_quota import UsageQuotaService


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _user(factory, email: str) -> uuid.UUID:
    with factory() as db:
        user = models.User(
            email=email,
            username=email.split("@", 1)[0],
            password_hash="test-only",
            display_name="Quota User",
        )
        db.add(user)
        db.commit()
        return user.id


def test_quota_enforces_user_and_global_daily_limits():
    factory = _session_factory()
    user_one = _user(factory, "quota-one@example.com")
    user_two = _user(factory, "quota-two@example.com")
    service = UsageQuotaService(
        Settings(
            database_url="sqlite:///:memory:",
            use_pgvector=False,
            daily_model_call_limit=1,
            global_daily_model_limit=2,
        ),
        factory,
    )

    assert service.reserve(user_one, provider="qwen", model_name="qwen-plus", endpoint="chat")
    assert not service.reserve(user_one, provider="qwen", model_name="qwen-plus", endpoint="chat")
    assert service.reserve(user_two, provider="qwen", model_name="qwen-plus", endpoint="chat")
    assert not service.reserve(None, provider="qwen", model_name="qwen-plus", endpoint="system")

    snapshot = service.snapshot(user_two)
    assert snapshot.user_used == 1
    assert snapshot.global_used == 2
    assert snapshot.live_calls_available is False

    provider = ModelProvider(
        Settings(
            llm_provider="qwen",
            dashscope_api_key="test-key-never-sent",
            database_url="sqlite:///:memory:",
            use_pgvector=False,
            daily_model_call_limit=1,
            global_daily_model_limit=2,
        ),
        user_id=user_two,
        endpoint="chat",
        quota_service=service,
    )
    assert provider.has_live_model() is False
    assert provider.chat_model() is None
    assert provider.quota_exhausted is True


def test_demo_initialization_is_idempotent_and_keeps_state():
    factory = _session_factory()
    settings = Settings(
        database_url="sqlite:///:memory:",
        use_pgvector=False,
        demo_mode=True,
        demo_email="demo@example.com",
        demo_password="demo-password-123",
    )

    with factory() as db:
        service = DemoAccountService(db, settings)
        first = service.ensure_today()
        second = service.ensure_today()
        assert first is not None and second is not None
        assert first.id == second.id

        state = db.scalar(
            select(models.DemoResetState).where(models.DemoResetState.demo_key == "public-demo")
        )
        assert state is not None
        assert state.reset_count == 1
        assert db.scalar(select(models.User).where(models.User.email == "demo@example.com"))
