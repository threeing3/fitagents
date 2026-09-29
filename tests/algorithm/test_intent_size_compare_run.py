"""Keep the model-size comparison prompt and output protocol stable."""

import json
from pathlib import Path

import pytest

from algorithm.evaluation import intent_size_compare_run as runner
from algorithm.inference.intent_catalog import AgentIntentCatalog

ROOT = Path(__file__).resolve().parents[2]
PROMPT = ROOT / "algorithm/evaluation/prompts/intent_size_compare_v1.txt"


def test_prompt_lists_every_legal_intent_and_the_output_fields() -> None:
    prompt = PROMPT.read_text(encoding="utf-8")
    assert prompt.startswith("/no_think")
    for label in AgentIntentCatalog.VALID_INTENTS:
        assert f"- {label}：" in prompt
    for field in (
        "primary_intent",
        "secondary_intents",
        "risk_level",
        "needs_clarification",
        "reason_codes",
    ):
        assert field in prompt


def test_request_payload_is_identical_except_model_id_and_message() -> None:
    first = runner.request_payload("fixed", "one", "4b")
    second = runner.request_payload("fixed", "two", "14b")
    assert first["temperature"] == second["temperature"] == 0
    assert first["seed"] == second["seed"] == 42
    assert first["max_tokens"] == second["max_tokens"] == 256
    assert first["messages"][0] == second["messages"][0]
    assert first["messages"][1]["content"] == "one"
    assert second["messages"][1]["content"] == "two"


def test_run_cases_writes_case_ids_and_refuses_overwrite(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_infer(endpoint, payload, timeout_seconds):
        calls.append((endpoint, payload, timeout_seconds))
        return {"text": '{"primary_intent":"general_chat"}', "usage": None}

    monkeypatch.setattr(runner, "infer_one", fake_infer)
    output = tmp_path / "predictions.jsonl"
    rows = [{"case_id": "p1", "user_message": "你好"}]
    runner.run_cases(
        rows,
        prompt="fixed",
        prompt_id="prompt-v1",
        model_id="4b",
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        output=output,
        timeout_seconds=3,
    )
    record = json.loads(output.read_text(encoding="utf-8").strip())
    assert record["case_id"] == "p1"
    assert record["model_id"] == "4b"
    assert record["prompt_id"] == "prompt-v1"
    assert len(calls) == 1
    with pytest.raises(FileExistsError):
        runner.run_cases(
            rows,
            prompt="fixed",
            prompt_id="prompt-v1",
            model_id="4b",
            endpoint="http://127.0.0.1:8080/v1/chat/completions",
            output=output,
            timeout_seconds=3,
        )
