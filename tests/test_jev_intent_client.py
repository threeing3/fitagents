import asyncio
import copy

import httpx
import pytest

from fast_api.app.services.jev_intent_client import STATES, TASKS, JevIntentClient


def valid_body():
    answers = {
        task: {
            "type": "choice",
            "choice": "absent",
            "confidence": 0.98,
            "probabilities": {state: float(state == "absent") for state in STATES},
        }
        for task in TASKS
    }
    for task in ("training_log", "nutrition_advice"):
        answers[task]["choice"] = "requested"
        answers[task]["probabilities"] = {state: float(state == "requested") for state in STATES}
    answers["current_risk"] = {"type": "noul", "noul": 0.01}
    return {
        "model": JevIntentClient.MODEL,
        "answers": answers,
        "usage": {"input_tokens": 2000, "output_tokens": 100},
    }


def test_multitask_preserved_without_exit_permission():
    result = JevIntentClient.parse_response(valid_body())
    assert result.succeeded
    assert result.task_states["training_log"] == "requested"
    assert result.task_states["nutrition_advice"] == "requested"
    assert result.summary()["exit_authorized"] is False


@pytest.mark.parametrize(
    "kind", ["missing", "unknown", "nan", "bool", "sum", "version", "usage", "wrong_choice"]
)
def test_reject_invalid_responses(kind):
    body = copy.deepcopy(valid_body())
    answer = body["answers"]["training_log"]
    if kind == "missing":
        del body["answers"]["nutrition_log"]
    elif kind == "unknown":
        answer["choice"] = "write_database"
    elif kind == "nan":
        answer["confidence"] = float("nan")
    elif kind == "bool":
        answer["confidence"] = True
    elif kind == "sum":
        answer["probabilities"]["absent"] = 1
    elif kind == "version":
        body["model"] = "new-model"
    elif kind == "usage":
        body["usage"]["input_tokens"] = -1
    else:
        answer["choice"] = "absent"
    assert not JevIntentClient.parse_response(body).succeeded


def test_no_key_no_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Network must not be called")

    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    result = asyncio.run(JevIntentClient().classify("今天怎么练"))
    assert not result.attempted
    assert result.status == "not_configured"


@pytest.mark.parametrize(
    "status,expected", [(401, "unauthorized"), (429, "rate_limited"), (500, "http_error")]
)
def test_failure_no_retries_no_key_leak(monkeypatch, status, expected):
    calls = []

    async def post(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = asyncio.run(JevIntentClient("private-test-key").classify("今天怎么练"))
    assert result.status == expected
    assert len(calls) == 1
    assert "private-test-key" not in str(result.summary())


def test_request_has_all_tasks_and_no_account_profile():
    body = JevIntentClient.request_body("先记录训练，不要改计划")
    assert set(body["questions"]) == set(TASKS) | {"current_risk"}
    assert set(body["state"]) == {"current_user_message"}
    assert "negated" in body["questions"]["training_plan"]["criteria"]


def test_unhashable_choice_is_rejected_without_crash():
    body = valid_body()
    body["answers"]["training_log"]["choice"] = ["requested"]
    assert not JevIntentClient.parse_response(body).succeeded


def test_timeout_is_not_retried(monkeypatch):
    calls = []

    async def post(self, url, **kwargs):
        calls.append(url)
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = asyncio.run(JevIntentClient("private-test-key").classify("记录跑步"))
    assert result.status == "timeout"
    assert len(calls) == 1
