"""Bounded client for the optional fine-tuned intent inference service."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from fast_api.app.core.config import Settings, get_settings


@dataclass
class IntentInferenceResult:
    attempted: bool
    succeeded: bool
    status: str
    payload: dict[str, Any] | None = None
    model_version: str | None = None
    latency_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    http_status: int | None = None


class IntentInferenceClient:
    """Call a private adapter endpoint without exposing its key or raw response."""

    def __init__(self, settings: Settings | None = None, *, timeout_seconds: float = 8.0):
        self.settings = settings or get_settings()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(getattr(self.settings, "adapter_inference_url", None))

    async def classify(
        self,
        message: str,
        rule_decision: dict[str, Any],
        profile_summary: dict[str, Any] | None = None,
    ) -> IntentInferenceResult:
        if not self.configured:
            return IntentInferenceResult(False, False, "not_configured")

        headers = {"Content-Type": "application/json"}
        adapter_key = getattr(self.settings, "adapter_inference_key", None)
        if adapter_key:
            headers["Authorization"] = f"Bearer {adapter_key}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout_seconds) as client:
                response = await client.post(
                    str(getattr(self.settings, "adapter_inference_url")),
                    headers=headers,
                    json={
                        "schema_version": "intent_decision_v2",
                        "message": message,
                        "rule_decision": rule_decision,
                        "profile_summary": profile_summary or {},
                    },
                )
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException:
            return self._failure("timeout", started)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            status = (
                "unauthorized"
                if status_code in {401, 403}
                else "invalid_model_output"
                if status_code == 422
                else "service_unavailable"
                if status_code >= 500
                else "request_rejected"
            )
            return self._failure(status, started, http_status=status_code)
        except (httpx.HTTPError, ValueError, TypeError):
            return self._failure("request_failed", started)

        payload = body.get("decision", body) if isinstance(body, dict) else None
        if not isinstance(payload, dict) or not payload.get("primary_intent"):
            return self._failure("invalid_payload", started)
        return IntentInferenceResult(
            attempted=True,
            succeeded=True,
            status="available",
            payload=payload,
            model_version=str(body.get("model_version") or "unknown"),
            latency_ms=self._elapsed(started),
            usage=body.get("usage", {}) if isinstance(body.get("usage"), dict) else {},
        )

    def _failure(
        self,
        status: str,
        started: float,
        *,
        http_status: int | None = None,
    ) -> IntentInferenceResult:
        return IntentInferenceResult(
            True,
            False,
            status,
            latency_ms=self._elapsed(started),
            http_status=http_status,
        )

    @staticmethod
    def _elapsed(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)
