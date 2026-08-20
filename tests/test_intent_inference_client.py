import asyncio
from types import SimpleNamespace

import httpx

from fast_api.app.services.intent_inference_client import IntentInferenceClient


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        adapter_inference_url="https://intent.example/v1/intent/classify",
        adapter_inference_key="private-test-key",
    )


def test_client_distinguishes_auth_failure(monkeypatch):
    async def fake_post(self, *args, **kwargs):
        request = httpx.Request("POST", args[0])
        return httpx.Response(401, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = asyncio.run(IntentInferenceClient(_settings()).classify("测试", {}))

    assert result.attempted is True
    assert result.succeeded is False
    assert result.status == "unauthorized"
    assert result.http_status == 401


def test_client_distinguishes_invalid_model_output(monkeypatch):
    async def fake_post(self, *args, **kwargs):
        request = httpx.Request("POST", args[0])
        return httpx.Response(422, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = asyncio.run(IntentInferenceClient(_settings()).classify("测试", {}))

    assert result.status == "invalid_model_output"
    assert result.http_status == 422


def test_client_reports_success_without_exposing_raw_response(monkeypatch):
    async def fake_post(self, *args, **kwargs):
        request = httpx.Request("POST", args[0])
        return httpx.Response(
            200,
            request=request,
            json={
                "decision": {"primary_intent": "general_chat"},
                "model_version": "qwen3-adapter-test",
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = asyncio.run(IntentInferenceClient(_settings()).classify("测试", {}))

    assert result.succeeded is True
    assert result.model_version == "qwen3-adapter-test"
    assert result.usage == {"prompt_tokens": 12, "completion_tokens": 8}
