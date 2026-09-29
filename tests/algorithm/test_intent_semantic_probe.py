import json
from pathlib import Path

import pytest

from algorithm.evaluation.intent_semantic_probe import check_decision, evaluate, load_cases
from fast_api.app.services.intent_decision import IntentDecision


def test_probe_is_synthetic_development_diagnostic_with_unique_pairs():
    dataset, cases = load_cases()

    assert dataset["source"] == "assistant_authored_synthetic"
    assert dataset["human_review_status"] == "not_reviewed"
    assert len(cases) == 12
    assert len({row["case_id"] for row in cases}) == 12
    assert set(row["family"] for row in cases) == {
        "current_vs_absent_symptom",
        "self_vs_other_red_flag",
        "request_vs_negated_plan",
        "profile_update_vs_correction",
        "query_vs_new_log",
        "weekly_vs_monthly_review",
    }


def test_probe_rejects_training_partition(tmp_path: Path):
    path = tmp_path / "wrong.json"
    path.write_text('{"partition":"train","training_eligible":true,"cases":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="development diagnostic"):
        load_cases(path)


def test_probe_rejects_unknown_intent_label(tmp_path: Path):
    dataset, cases = load_cases()
    cases[0]["required_intents"] = ["unknown_action"]
    path = tmp_path / "bad_label.json"
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown intent label"):
        load_cases(path)


def test_check_decision_catches_forbidden_intent_and_plan_permission():
    row = {
        "required_intents": ["memory_query"],
        "forbidden_intents": ["training_log"],
        "minimum_risk_level": "low",
        "maximum_risk_level": "low",
        "generate_plan_allowed": False,
    }
    decision = IntentDecision(
        primary_intent="memory_query",
        secondary_intents=["training_log"],
        risk_level="low",
        allowed_actions={"generate_plan": True},
    )

    assert check_decision(row, decision) == {
        "required_intents": True,
        "forbidden_intents": False,
        "risk_bounds": True,
        "plan_permission": False,
    }


def test_evaluation_keeps_each_path_and_case_separate():
    _, cases = load_cases()

    details, summary = evaluate(cases)

    assert len(details) == 24
    assert len({(row["case_id"], row["path"]) for row in details}) == 24
    assert summary["cases"] == 12
    assert summary["training_eligible"] is False
    assert set(summary["paths"]) == {"rule_only", "rule_with_protocol"}
