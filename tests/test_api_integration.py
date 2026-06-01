"""Integration tests for API endpoints using FastAPI TestClient.

These tests verify:
- Auth endpoints (register, login, me) return correct HTTP codes
- Protected endpoints reject unauthenticated requests with 401
- JWT token flow works end-to-end with an in-memory SQLite database
"""

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base, get_db


# ---- Override settings for testing ----
@patch("fast_api.app.core.config.get_settings")
def _make_test_app(mock_settings):
    """Create a TestClient with in-memory SQLite and JWT secret."""
    mock_settings.return_value = Settings(
        database_url="sqlite:///:memory:",
        jwt_secret_key="test-secret-key-for-integration-tests",
        jwt_algorithm="HS256",
        jwt_expire_minutes=60,
        use_pgvector=False,
        cors_origins="http://localhost:5173",
        llm_provider="offline",
        embedding_provider="offline",
    )

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fast_api.app.main import app
    app.dependency_overrides[get_db] = override_get_db

    return TestClient(app), TestSessionLocal


def _create_client_and_db():
    """Module-level helper to create client and session factory."""
    # Use a fresh in-memory engine per client
    test_settings = Settings(
        database_url="sqlite:///:memory:",
        jwt_secret_key="test-secret-key-for-integration-tests",
        jwt_algorithm="HS256",
        jwt_expire_minutes=60,
        use_pgvector=False,
        cors_origins="http://localhost:5173",
        llm_provider="offline",
        embedding_provider="offline",
    )

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    # Import models so they register with Base
    from fast_api.app.db import models as _models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Need to patch settings before importing main
    with patch("fast_api.app.core.config.get_settings") as mock_settings:
        mock_settings.return_value = test_settings
        from fast_api.app.main import app
        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app), TestSessionLocal


# ============================================================
# Health check
# ============================================================

def test_health_endpoint_returns_ok():
    client, _ = _create_client_and_db()
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "provider" in data


# ============================================================
# Auth: Registration
# ============================================================

def test_register_creates_user_and_returns_jwt():
    client, session_factory = _create_client_and_db()

    response = client.post("/v1/auth/register", json={
        "email": "test@example.com",
        "password": "secure123",
        "display_name": "Test User",
    })

    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["email"] == "test@example.com"
    assert data["display_name"] == "Test User"
    assert data["username"] == "test.user"
    assert data["user_id"]


def test_register_duplicate_email_returns_409():
    client, _ = _create_client_and_db()

    # First registration
    client.post("/v1/auth/register", json={
        "email": "dupe@example.com",
        "password": "secure123",
    })

    # Second registration with same email
    response = client.post("/v1/auth/register", json={
        "email": "dupe@example.com",
        "password": "secure456",
    })

    assert response.status_code == 409


def test_register_short_password_returns_422():
    client, _ = _create_client_and_db()

    response = client.post("/v1/auth/register", json={
        "email": "short@example.com",
        "password": "ab",  # Too short (min 6)
    })

    assert response.status_code == 422


def test_register_invalid_email_returns_422():
    client, _ = _create_client_and_db()

    response = client.post("/v1/auth/register", json={
        "email": "not-an-email",
        "password": "secure123",
    })

    assert response.status_code == 422


# ============================================================
# Auth: Login
# ============================================================

def test_login_with_valid_credentials_returns_jwt():
    client, _ = _create_client_and_db()

    # Register first
    client.post("/v1/auth/register", json={
        "email": "login@example.com",
        "password": "mypassword",
        "display_name": "Login User",
    })

    # Then login
    response = client.post("/v1/auth/login", json={
        "email": "login@example.com",
        "password": "mypassword",
    })

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["display_name"] == "Login User"


def test_login_with_username_returns_jwt():
    client, _ = _create_client_and_db()

    client.post("/v1/auth/register", json={
        "email": "username-login@example.com",
        "username": "coach_dev",
        "password": "mypassword",
        "display_name": "Coach Dev",
    })

    response = client.post("/v1/auth/login", json={
        "identifier": "coach_dev",
        "password": "mypassword",
    })

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["email"] == "username-login@example.com"
    assert data["username"] == "coach_dev"


def test_login_wrong_password_returns_401():
    client, _ = _create_client_and_db()

    client.post("/v1/auth/register", json={
        "email": "wrong@example.com",
        "password": "correct123",
    })

    response = client.post("/v1/auth/login", json={
        "email": "wrong@example.com",
        "password": "wrongpassword",
    })

    assert response.status_code == 401


def test_login_nonexistent_user_returns_401():
    client, _ = _create_client_and_db()

    response = client.post("/v1/auth/login", json={
        "email": "nobody@example.com",
        "password": "whatever",
    })

    assert response.status_code == 401


# ============================================================
# Auth: Get current user
# ============================================================

def test_me_returns_user_info_with_valid_token():
    client, _ = _create_client_and_db()

    register_resp = client.post("/v1/auth/register", json={
        "email": "me@example.com",
        "password": "secure123",
        "display_name": "Me User",
    })
    token = register_resp.json()["access_token"]

    response = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "me@example.com"
    assert data["display_name"] == "Me User"


def test_update_me_updates_username_and_avatar():
    client, _ = _create_client_and_db()

    register_resp = client.post("/v1/auth/register", json={
        "email": "profile@example.com",
        "password": "secure123",
        "display_name": "Profile User",
    })
    token = register_resp.json()["access_token"]

    response = client.patch(
        "/v1/auth/me",
        json={
            "display_name": "Updated User",
            "username": "updated_user",
            "avatar_url": "https://example.com/avatar.png",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Updated User"
    assert data["username"] == "updated_user"
    assert data["avatar_url"] == "https://example.com/avatar.png"


def test_me_without_token_returns_401():
    client, _ = _create_client_and_db()

    response = client.get("/v1/auth/me")

    assert response.status_code == 401


def test_me_with_invalid_token_returns_401():
    client, _ = _create_client_and_db()

    response = client.get("/v1/auth/me", headers={"Authorization": "Bearer invalid.token.here"})

    assert response.status_code == 401


# ============================================================
# Protected endpoints: Coach platform
# ============================================================

def test_coach_endpoints_reject_unauthenticated():
    client, _ = _create_client_and_db()

    # All coach endpoints should return 401 without a token
    endpoints = [
        ("POST", "/v1/chat/sessions", {"display_name": "Test", "title": "Chat"}),
        ("POST", "/v1/chat/messages", {"session_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "message": "hi"}),
        ("POST", "/v1/profiles", {"age": 25}),
        ("POST", "/v1/checkins/daily", {"sleep_hours": 7}),
        ("POST", "/v1/workouts/logs", {"workout_name": "test"}),
        ("POST", "/v1/plans/generate", {"user_id": str(uuid.uuid4())}),
        ("POST", "/v1/plans/adjust", {"user_id": str(uuid.uuid4())}),
        ("GET", f"/v1/users/{uuid.uuid4()}/dashboard", None),
        ("GET", f"/v1/agent-runs/{uuid.uuid4()}", None),
    ]

    for method, path, body in endpoints:
        if method == "POST":
            resp = client.post(path, json=body or {})
        else:
            resp = client.get(path)
        assert resp.status_code == 401, f"{method} {path} should return 401, got {resp.status_code}"


def test_create_chat_session_with_valid_token():
    client, _ = _create_client_and_db()

    # Register and get token
    resp = client.post("/v1/auth/register", json={
        "email": "chat@example.com",
        "password": "secure123",
        "display_name": "Chat User",
    })
    token = resp.json()["access_token"]

    # Create chat session
    response = client.post(
        "/v1/chat/sessions",
        json={"display_name": "Chat User", "title": "My Session"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["title"] == "My Session"


def test_dashboard_returns_403_for_other_users_data():
    client, _ = _create_client_and_db()

    # Register two users
    r1 = client.post("/v1/auth/register", json={
        "email": "user1@example.com", "password": "secure123",
    })
    token1 = r1.json()["access_token"]
    user1_id = r1.json()["user_id"]

    r2 = client.post("/v1/auth/register", json={
        "email": "user2@example.com", "password": "secure456",
    })
    token2 = r2.json()["access_token"]

    # User 2 tries to access user 1's dashboard — should be rejected
    response = client.get(
        f"/v1/users/{user1_id}/dashboard",
        headers={"Authorization": f"Bearer {token2}"},
    )

    assert response.status_code == 403


def test_chat_sessions_and_agent_runs_are_user_isolated():
    client, session_factory = _create_client_and_db()

    r1 = client.post("/v1/auth/register", json={
        "email": "isolated1@example.com", "password": "secure123",
    })
    token1 = r1.json()["access_token"]
    r2 = client.post("/v1/auth/register", json={
        "email": "isolated2@example.com", "password": "secure456",
    })
    token2 = r2.json()["access_token"]

    session = client.post(
        "/v1/chat/sessions",
        json={"display_name": "One", "title": "Private Session"},
        headers={"Authorization": f"Bearer {token1}"},
    ).json()

    response = client.get(
        f"/v1/chat/sessions/{session['session_id']}/messages",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert response.status_code == 403

    with session_factory() as db:
        run = models.AgentRun(
            user_id=uuid.UUID(r1.json()["user_id"]),
            session_id=uuid.UUID(session["session_id"]),
            run_type="chat",
            status="completed",
            nodes=[],
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

    response = client.get(
        f"/v1/agent-runs/{run_id}",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert response.status_code == 403


# ============================================================
# Protected endpoints: Memory
# ============================================================

def test_memory_endpoints_reject_unauthenticated():
    client, _ = _create_client_and_db()

    endpoints = [
        ("POST", "/v1/memory/items", {"memory_type": "preference", "category": "test", "content": "test"}),
        ("GET", "/v1/memory/items", None),
        ("GET", "/v1/memory/catalog", None),
        ("POST", "/v1/memory/search", {"query": "test"}),
    ]

    for method, path, body in endpoints:
        if method == "POST":
            resp = client.post(path, json=body or {})
        else:
            resp = client.get(path)
        assert resp.status_code == 401, f"{method} {path} should return 401, got {resp.status_code}"


def test_create_memory_item_with_valid_token():
    client, _ = _create_client_and_db()

    resp = client.post("/v1/auth/register", json={
        "email": "memory@example.com",
        "password": "secure123",
    })
    token = resp.json()["access_token"]

    response = client.post(
        "/v1/memory/items",
        json={
            "memory_type": "stable_preference",
            "category": "preference",
            "content": "User prefers morning workouts",
            "summary": "Morning workout preference",
            "importance": 0.7,
            "confidence": 0.9,
            "source": "chat",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["memory_type"] == "stable_preference"
    assert data["category"] == "preference"


def test_list_memory_items_with_valid_token():
    client, _ = _create_client_and_db()

    resp = client.post("/v1/auth/register", json={
        "email": "list@example.com",
        "password": "secure123",
    })
    token = resp.json()["access_token"]

    # Create a memory item first
    client.post(
        "/v1/memory/items",
        json={
            "memory_type": "stable_preference",
            "category": "preference",
            "content": "Test memory",
            "summary": "Test",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.get(
        "/v1/memory/items",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


# ============================================================
# Token expiry and edge cases
# ============================================================

def test_expired_token_returns_401():
    """Test that a token with an expired 'exp' claim is rejected."""
    client, _ = _create_client_and_db()

    # Create a token that expired in the past using a known secret
    import time
    from jose import jwt

    expired_token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "email": "expired@example.com",
            "exp": int(time.time()) - 3600,  # 1 hour ago
            "iat": int(time.time()) - 7200,
        },
        "test-secret-key-for-integration-tests",
        algorithm="HS256",
    )

    response = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert response.status_code == 401


def test_malformed_auth_header_returns_401():
    client, _ = _create_client_and_db()

    response = client.get(
        "/v1/auth/me",
        headers={"Authorization": "NotBearer token"},
    )

    assert response.status_code == 401


def test_no_auth_header_returns_401():
    client, _ = _create_client_and_db()

    response = client.get("/v1/auth/me")

    assert response.status_code == 401


# ============================================================
# Auth flow: full cycle
# ============================================================

def test_full_auth_flow_register_login_me():
    """End-to-end: register -> login -> me -> protected endpoint."""
    client, _ = _create_client_and_db()

    # 1. Register
    register_resp = client.post("/v1/auth/register", json={
        "email": "fullflow@example.com",
        "password": "fullflow123",
        "display_name": "Full Flow User",
    })
    assert register_resp.status_code == 201
    reg_token = register_resp.json()["access_token"]
    reg_user_id = register_resp.json()["user_id"]

    # 2. Verify me with register token
    me_resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {reg_token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["display_name"] == "Full Flow User"

    # 3. Login (fresh token)
    login_resp = client.post("/v1/auth/login", json={
        "email": "fullflow@example.com",
        "password": "fullflow123",
    })
    assert login_resp.status_code == 200
    login_token = login_resp.json()["access_token"]
    assert login_resp.json()["user_id"] == reg_user_id

    # 4. Use token for protected endpoint
    chat_resp = client.post(
        "/v1/chat/sessions",
        json={"display_name": "Full Flow User", "title": "Test Chat"},
        headers={"Authorization": f"Bearer {login_token}"},
    )
    assert chat_resp.status_code == 200
    assert "session_id" in chat_resp.json()

    # 5. Logout (client-side only — token is still valid but client discards it)
    # No server-side logout; just verify token is still accepted
    me_after = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {login_token}"})
    assert me_after.status_code == 200
