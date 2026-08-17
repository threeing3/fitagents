"""Product-safety regression tests for the public demo release."""

from unittest.mock import patch

import pytest

from fast_api.app.core.config import Settings
from fast_api.app.core.security import hash_password
from fast_api.app.db import models
from tests.test_api_integration import _create_client_and_db


def test_registration_sets_http_only_cookie_and_cookie_auth_works():
    client, _ = _create_client_and_db()

    response = client.post(
        "/v1/auth/register",
        json={
            "email": "cookie@example.com",
            "password": "cookie-password",
            "display_name": "Cookie User",
        },
    )

    assert response.status_code == 201
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert client.get("/v1/auth/me").status_code == 200

    logout = client.post("/v1/auth/logout")
    assert logout.status_code == 204
    assert client.get("/v1/auth/me").status_code == 401


def test_invite_code_is_required_when_configured():
    client, _ = _create_client_and_db()

    with patch("fast_api.app.api.auth_api.settings.invite_code", "invite-12345"):
        rejected = client.post(
            "/v1/auth/register",
            json={"email": "wrong-invite@example.com", "password": "password-12345"},
        )
        accepted = client.post(
            "/v1/auth/register",
            json={
                "email": "right-invite@example.com",
                "password": "password-12345",
                "invite_code": "invite-12345",
            },
        )

    assert rejected.status_code == 403
    assert accepted.status_code == 201


def test_metrics_require_constant_time_token_authentication():
    client, _ = _create_client_and_db()

    with patch("fast_api.app.main.settings.metrics_token", "metrics-token-with-24-chars"):
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"X-Metrics-Token": "wrong"}).status_code == 401
        response = client.get(
            "/metrics",
            headers={"Authorization": "Bearer metrics-token-with-24-chars"},
        )

    assert response.status_code == 200
    assert "fitness_api_requests_total" in response.text


def test_default_production_configuration_is_rejected():
    settings = Settings(environment="production")

    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        settings.validate_runtime()


def test_complete_production_configuration_passes_validation():
    settings = Settings(
        _env_file=None,
        environment="production",
        DATABASE_URL="postgresql+psycopg://user:pass@db.example.com/fitness",
        JWT_SECRET_KEY="j" * 64,
        INVITE_CODE="invite-code-123",
        METRICS_TOKEN="m" * 32,
        AUTH_COOKIE_SECURE=True,
        CORS_ORIGINS="https://fitness.example.com",
        LLM_PROVIDER="qwen",
        DASHSCOPE_API_KEY="configured-at-runtime",
    )

    settings.validate_runtime()


def test_existing_short_password_account_can_still_log_in():
    client, session_factory = _create_client_and_db()
    with session_factory() as db:
        user = models.User(
            email="legacy@example.com",
            username="legacy",
            password_hash=hash_password("oldpass"),
            display_name="Legacy User",
        )
        db.add(user)
        db.flush()
        db.add(models.UserProfile(user_id=user.id))
        db.commit()

    response = client.post(
        "/v1/auth/login",
        json={"identifier": "legacy", "password": "oldpass"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "legacy@example.com"


def test_algorithm_summary_marks_fixed_and_simulated_evidence():
    client, _ = _create_client_and_db()

    response = client.get("/v1/algorithm/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["business_outcomes"] == {
        "label": "simulated_outcome",
        "online_claim": False,
    }
    assert payload["dpo"]["enabled"] is False
    assert payload["release_stage"] == "maturity_03_algorithms"
    assert all(item["source"] != "expert_labeled" for item in payload["datasets"])
    assert any(
        item["name"] == "intent_fixed" and item["size"] == 120 for item in payload["datasets"]
    )
    assert any(
        item["name"] == "business_fixed_pass" and item["value"] == 38 for item in payload["metrics"]
    )
    assert payload["retrieval"]["vector_status"] == "vector unavailable"

    experiment = client.get("/v1/algorithm/experiments/maturity_03_algorithms_20260809")
    assert experiment.status_code == 200
    assert experiment.json()["provenance"]["training_eligible"] is False
    assert "predictions" not in experiment.text


def test_invalid_image_payload_is_rejected_before_model_call():
    client, _ = _create_client_and_db()
    registration = client.post(
        "/v1/auth/register",
        json={"email": "image@example.com", "password": "password-12345"},
    )
    assert registration.status_code == 201

    response = client.post(
        "/v1/nutrition/recognize",
        files={"image": ("fake.png", b"not-a-real-image", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Image could not be safely decoded."


def test_failed_login_does_not_log_password(caplog):
    client, _ = _create_client_and_db()
    secret = "do-not-log-this-password"

    response = client.post(
        "/v1/auth/login",
        json={"identifier": "missing@example.com", "password": secret},
    )

    assert response.status_code == 401
    assert secret not in caplog.text
