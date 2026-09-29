"""Authenticated, read-only status endpoint with isolated synthetic state."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fast_api.app.api.coach_platform import coach_router
from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import Base, get_db


def test_status_endpoint_uses_authenticated_identity_and_validates_key():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            user = models.User(id=uuid.uuid4(), email="status@example.test", password_hash="none")
            db.add(user)
            db.flush()
            session = models.ConversationSession(user_id=user.id, title="Status")
            db.add(session)
            db.flush()
            sid = session.id
            db.add(
                models.IdempotencyRecord(
                    user_id=user.id,
                    operation="chat",
                    idempotency_key="reserved",
                    request_json={"session_id": str(sid)},
                    status="processing",
                )
            )
            db.commit()
            db.refresh(user)
            db.expunge(user)

        def database():
            with Session(engine) as reader:
                yield reader

        app = FastAPI()
        app.include_router(coach_router, prefix="/v1")
        app.dependency_overrides[get_db] = database
        client = TestClient(app)
        path = f"/v1/chat/requests/status?session_id={sid}"
        assert client.get(path, headers={"Idempotency-Key": "reserved"}).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: user
        response = client.get(path, headers={"Idempotency-Key": "reserved"})
        assert response.status_code == 200
        assert response.json()["status"] == "unconfirmed"
        assert response.json()["confirmed_writes"] == []
        assert client.get(path).status_code == 422
        assert client.get(path, headers={"Idempotency-Key": "x" * 129}).status_code == 422
        assert (
            client.get(
                f"/v1/chat/requests/status?session_id={uuid.uuid4()}",
                headers={"Idempotency-Key": "reserved"},
            ).status_code
            == 404
        )
    finally:
        engine.dispose()
