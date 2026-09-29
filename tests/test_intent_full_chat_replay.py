"""Verify an actual offline chat replay against freshly read committed state."""

import json
from pathlib import Path

from algorithm.evaluation.intent_full_chat_replay import (
    DATASET,
    load_cases,
    replay_case,
    run_replay,
)
from fast_api.app.services.coach_agent import CoachAgentService

PROSPECTIVE_CASES = (
    Path(__file__).resolve().parents[1]
    / "algorithm/datasets/fixtures/intent_prospective_diagnostic_v1.json"
)


def test_replay_rejects_caught_runtime_failure(tmp_path, monkeypatch):
    async def fail_reply(*args, **kwargs):
        raise RuntimeError("synthetic response failure")

    monkeypatch.setattr(CoachAgentService, "_coaching_reply", fail_reply)
    outcome = replay_case(load_cases()[0], tmp_path / "agent_logs")
    assert outcome["checks"]["no_runtime_error"] is False
    assert outcome["passed"] is False
    assert outcome["persisted"]["runtime_errors"][0]["node"] == "RuntimeError"


def test_replay_stays_offline_when_application_loaded_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "qwen")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-invalid-key")
    monkeypatch.setenv("ADAPTER_INFERENCE_URL", "http://127.0.0.1:1/not-used")
    monkeypatch.setenv("AGENT_RUNTIME_MODE", "llm_driven")
    monkeypatch.setenv("CODE_DRIVEN_PLANNER", "llm")
    outcome = replay_case(load_cases()[0], tmp_path / "agent_logs")
    assert outcome["passed"]
    assert outcome["runtime_route"]["mode"] == "code_driven"
    assert outcome["runtime_route"]["intent_decision"]["provenance"]["model_attempted"] is False


def test_fixture_is_fixed_synthetic_and_unique():
    cases = load_cases(DATASET)
    assert [case["case_id"] for case in cases] == [
        "record_then_query",
        "explicit_plan_negation",
        "third_person_symptom",
    ]


def test_full_chat_replay_preserves_actual_failures(tmp_path):
    output_dir = tmp_path / "full_replay"
    summary = run_replay(output_dir)
    assert summary["case_count"] == 3
    cases = {
        case_id: json.loads((output_dir / f"{case_id}.json").read_text(encoding="utf-8"))
        for case_id in ["record_then_query", "explicit_plan_negation", "third_person_symptom"]
    }
    for item in cases.values():
        assert item["checks"]["run_completed"]
        assert item["checks"]["no_runtime_error"]
        assert item["checks"]["replay_recorded"]
        assert item["checks"]["reply_persisted"]
        assert item["checks"]["tool_trace_persisted"]
        assert item["response_review_status"] == "not_reviewed"
        assert item["persisted"]["agent_log_path"]
    assert cases["explicit_plan_negation"]["state_delta"]["plan"] == 0
    assert cases["third_person_symptom"]["state_delta"]["user_risk_note"] == 0
    record = cases["record_then_query"]
    assert "training.log.write" in record["intent_summary"]["candidate_tools"]
    assert record["tool_call_counts"].get("training.log.write", 0) == 1
    assert record["state_delta"]["workout_log"] == 1
    assert "已记录：哑铃训练，30分钟" in record["assistant_message"]
    assert record["checks"]["workout_log_delta"] == (record["state_delta"]["workout_log"] == 1)


def test_recorded_prediction_enters_existing_gate_without_live_model(tmp_path):
    case = {
        "case_id": "recorded-query",
        "message": "前天慢跑了三公里，这条已经在日志里。告诉我记录的配速是多少。",
        "expected": {"workout_log_delta": 0, "plan_delta": 0},
    }
    prediction = {
        "primary_intent": "memory_query",
        "secondary_intents": [],
        "risk_level": "low",
        "needs_clarification": False,
    }
    outcome = replay_case(case, tmp_path / "recorded_logs", recorded_model_payload=prediction)
    assert outcome["recorded_prediction_supplied"]
    assert outcome["recorded_prediction_consumed"]
    assert outcome["runtime_route"]["intent_decision"]["provenance"]["local_model_used"]
    assert outcome["state_delta"]["workout_log"] == 0
    assert outcome["profile_before"] == outcome["profile_after"]


def test_recorded_prediction_does_not_override_refinement_gate(tmp_path):
    case = {
        "case_id": "gated-off",
        "message": "我刚完成30分钟跑步，请记下来。",
        "expected": {"workout_log_delta": 1, "plan_delta": 0},
    }
    prediction = {
        "primary_intent": "training_log",
        "secondary_intents": ["memory_query"],
        "risk_level": "low",
        "needs_clarification": False,
    }
    outcome = replay_case(case, tmp_path / "gated_logs", recorded_model_payload=prediction)
    assert outcome["recorded_prediction_supplied"]
    assert not outcome["recorded_prediction_consumed"]
    assert not outcome["runtime_route"]["intent_decision"]["provenance"]["local_model_used"]


def test_profile_unchanged_expectation_reads_final_state(tmp_path):
    case = {
        "case_id": "profile-stability",
        "message": "我说‘胸口发紧’是在引用歌词，并不是我的症状；请安排明天慢跑。",
        "expected": {"profile_unchanged": True},
    }
    outcome = replay_case(case, tmp_path / "profile_logs")
    assert outcome["checks"]["profile_unchanged"]
    assert outcome["profile_before"] == outcome["profile_after"]


def test_current_breathing_or_chest_red_flags_receive_immediate_reply(tmp_path):
    rows = json.loads(PROSPECTIVE_CASES.read_text(encoding="utf-8"))
    selected = [row for row in rows if row["case_id"] in {"intent-prosp-009", "intent-prosp-012"}]
    for row in selected:
        outcome = replay_case(
            {
                "case_id": row["case_id"],
                "message": row["user_message"],
                "expected": {"workout_log_delta": 0, "plan_delta": 0},
            },
            tmp_path / row["case_id"],
        )
        assert outcome["passed"]
        assert "停止运动" in outcome["assistant_message"]
        assert "当地急救电话" in outcome["assistant_message"]
        assert "普通问答" not in outcome["assistant_message"]


def test_reply_content_requirement_fails_closed(tmp_path):
    outcome = replay_case(
        {
            "case_id": "missing_safety_text",
            "message": "你好。",
            "expected": {"reply_contains": ["当地急救电话"]},
        },
        tmp_path / "missing_safety_text",
    )
    assert not outcome["checks"]["reply_contains"]
    assert not outcome["passed"]
