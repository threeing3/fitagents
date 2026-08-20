"""Product-safety regression tests for the public demo release."""

import uuid
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
    assert payload["intent_inference"]["adapter_status"] in {
        "not_trained",
        "configured_unverified",
        "verified_offline",
        "verified_service_configured",
    }
    assert payload["intent_inference"]["online_result_claimed"] is False
    release = payload["intent_inference"]["verified_release"]
    assert release["status"] == "verified_offline"
    assert release["dataset"] == {
        "source": "fixed_challenge_test",
        "training_eligible": False,
        "cases": 120,
    }
    assert release["metrics"]["adapter_schema_valid_rate"] == 1.0
    assert release["metrics"]["adapter_risk_recall"] == 1.0
    assert release["claims"]["online_business_uplift"] is False
    assert release["claims"]["real_user_outcome"] is False

    experiment = client.get("/v1/algorithm/experiments/maturity_03_algorithms_20260809")
    assert experiment.status_code == 200
    assert experiment.json()["provenance"]["training_eligible"] is False
    assert "predictions" not in experiment.text


def test_agent_lab_challenge_report_is_test_only():
    client, _ = _create_client_and_db()

    response = client.get("/v1/algorithm/challenges/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cases"] == 120
    assert payload["partition"] == "test"
    assert payload["training_eligible"] is False
    assert payload["failure_examples"]


def test_intent_evaluation_summary_is_aggregate_and_test_only():
    client, _ = _create_client_and_db()

    response = client.get("/v1/algorithm/intent-evaluation/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset"]["cases"] == 120
    assert payload["dataset"]["partition"] == "test"
    assert payload["dataset"]["training_eligible"] is False
    assert payload["dataset"]["user_messages_exposed"] is False
    assert {row["id"] for row in payload["paths"]} == {
        "rule_only",
        "deepseek_all",
        "hybrid",
        "qwen3_adapter",
    }
    assert payload["adapter_delta_vs_base"] == 0.075
    assert all("user_message" not in row for row in payload["paths"])
    assert "predictions" not in response.text


def test_algorithm_compare_requires_login_and_reports_real_fallback_state():
    client, _ = _create_client_and_db()
    assert client.post("/v1/algorithm/compare", json={"message": "怎么练？"}).status_code == 401
    registration = client.post(
        "/v1/auth/register",
        json={"email": "compare@example.com", "password": "password-12345"},
    )
    assert registration.status_code == 201

    response = client.post(
        "/v1/algorithm/compare",
        json={"message": "我膝盖疼，但明天还能继续深蹲吗？"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_decision"]["risk_level"] in {"medium", "high", "critical"}
    assert payload["routing"]["rules_evaluated"] is True
    assert payload["routing"]["safety_override_applied"] is False
    assert payload["routing"]["safety_override_reasons"] == []
    assert payload["routing"]["local_model_status"] == "not_configured"
    assert payload["routing"]["local_model_used"] is False
    assert payload["routing"]["adapter_fallback_reason"] is None


def test_agent_lab_runs_are_authenticated_user_isolated_and_sanitized():
    client, session_factory = _create_client_and_db()
    assert client.get("/v1/algorithm/agent-runs").status_code == 401
    registration = client.post(
        "/v1/auth/register",
        json={"email": "trace@example.com", "password": "password-12345"},
    )
    assert registration.status_code == 201
    user_id = registration.json()["user_id"]

    with session_factory() as db:
        own_run = models.AgentRun(
            user_id=uuid.UUID(user_id),
            run_type="chat",
            status="completed",
            nodes=[
                {
                    "node": "IntentRouter",
                    "status": "completed",
                    "output": {"primary_intent": "training_plan", "raw_context": "secret"},
                },
                {
                    "node": "GuardrailCheck",
                    "status": "completed",
                    "output": {"action": "pass"},
                },
            ],
            summary="safe summary",
            log_path="C:/private/raw-trace.jsonl",
        )
        other = models.User(
            email="other-trace@example.com",
            password_hash=hash_password("password-12345"),
            display_name="Other",
        )
        db.add_all([own_run, other])
        db.flush()
        other_run = models.AgentRun(user_id=other.id, run_type="chat", nodes=[])
        db.add(other_run)
        db.flush()
        db.add(
            models.ToolCall(
                agent_run_id=own_run.id,
                tool_name="plan.generate",
                input_json={"private": "secret"},
                output_json={"private": "secret"},
                status="success",
            )
        )
        db.commit()
        own_id, other_id = str(own_run.id), str(other_run.id)

    listing = client.get("/v1/algorithm/agent-runs")
    assert listing.status_code == 200
    assert [row["run_id"] for row in listing.json()] == [own_id]
    detail = client.get(f"/v1/algorithm/agent-runs/{own_id}")
    assert detail.status_code == 200
    assert detail.json()["decision"]["tool_names"] == ["plan.generate"]
    assert detail.json()["privacy"] == "sanitized_projection_no_raw_user_context"
    assert "secret" not in detail.text
    assert "raw-trace" not in detail.text
    assert client.get(f"/v1/algorithm/agent-runs/{other_id}").status_code == 403


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
