import json

import pytest

pytest.importorskip("sklearn")

from algorithm.app_algorithms.multilabel_intent_baseline import (
    TfidfIntentBaseline,
    calibrate_secondary_thresholds,
    evaluate_baseline,
    multilabel_metrics,
)
from algorithm.data.multilabel_intent_dataset_factory import build_multilabel_intent_examples
from algorithm.evaluation.multilabel_data_audit import (
    audit_label_coverage,
    select_eligible_train_rows,
)


def _train_row(message: str, primary: str, secondary: list[str]) -> dict:
    return {
        "user_message": message,
        "retrieved_context": {},
        "assistant_response": json.dumps(
            {"primary_intent": primary, "secondary_intents": secondary}, ensure_ascii=False
        ),
    }


def _development_row(primary: str, secondary: list[str]) -> dict:
    return {
        "case_id": "case-1",
        "user_message": "安排训练并检查恢复",
        "expected_primary_intent": primary,
        "required_secondary_intents": secondary,
    }


def test_audit_exposes_unseen_and_invalid_labels():
    train = [
        _train_row("制定训练计划", "training_plan", ["nutrition_advice"]),
        _train_row("记录训练", "workout_logging", []),
    ]
    development = [_development_row("weekly_review", ["profile_update"])]

    report = audit_label_coverage(train, development)

    assert report["unseen_development_primary_labels"] == ["weekly_review"]
    assert report["unseen_development_secondary_labels"] == ["profile_update"]
    assert report["invalid_train_primary_labels"] == ["workout_logging"]
    assert report["training_ready"] is False


def test_train_selector_excludes_validation_test_and_ineligible_rows():
    rows = [
        {"split": "train", "training_eligible": True, "example_id": "keep"},
        {"split": "validation", "training_eligible": True, "example_id": "validation"},
        {"split": "test", "training_eligible": False, "example_id": "test"},
        {"split": "train", "training_eligible": False, "example_id": "excluded"},
    ]

    assert [row["example_id"] for row in select_eligible_train_rows(rows)] == ["keep"]


def test_multilabel_metrics_report_macro_micro_and_exact_match():
    metrics = multilabel_metrics(
        [{"training_plan", "nutrition_advice"}, {"recovery_check"}],
        [{"training_plan"}, {"recovery_check"}],
        ["nutrition_advice", "recovery_check", "training_plan"],
    )

    assert metrics["exact_match"] == 0.5
    assert metrics["micro_f1"] == 0.8
    assert metrics["per_label"]["nutrition_advice"]["recall"] == 0.0


def test_tfidf_baseline_uses_independent_secondary_heads():
    rows = [
        _train_row(f"训练计划增肌第{index}次", "training_plan", ["nutrition_advice"])
        for index in range(5)
    ] + [_train_row(f"疲劳恢复检查第{index}次", "recovery_check", []) for index in range(5)]
    model = TfidfIntentBaseline(threshold=0.5)
    model.fit(rows, ["nutrition_advice", "profile_update"])

    prediction = model.predict([{"user_message": "训练计划增肌", "retrieved_context": {}}])[0]

    assert prediction["primary_intent"] == "training_plan"
    assert "nutrition_advice" in prediction["secondary_intents"]
    assert "profile_update" not in prediction["secondary_intents"]
    assert prediction["secondary_probabilities"]["profile_update"] == 0.0


def test_threshold_must_be_strict_probability():
    with pytest.raises(ValueError, match="threshold"):
        TfidfIntentBaseline(threshold=1.0)


def test_evaluation_filters_non_train_and_ineligible_rows():
    train_rows = []
    for index in range(6):
        row = _train_row(f"训练计划{index}", "training_plan", ["nutrition_advice"])
        row.update({"split": "train", "training_eligible": True})
        train_rows.append(row)
    for index in range(6):
        row = _train_row(f"恢复检查{index}", "recovery_check", [])
        row.update({"split": "train", "training_eligible": True})
        train_rows.append(row)
    held_out = _train_row("开发集特有文本", "weekly_review", ["profile_update"])
    held_out.update({"split": "validation", "training_eligible": True})
    train_rows.append(held_out)
    development = [_development_row("training_plan", ["nutrition_advice"])]

    report = evaluate_baseline(train_rows, development, threshold=0.5)

    assert report["dataset"]["train_rows"] == 12
    assert report["audit"]["unseen_development_primary_labels"] == []


def test_threshold_calibration_uses_validation_rows_and_returns_each_label():
    rows = [row.to_dict() for row in build_multilabel_intent_examples(train_per_family=3)]
    train = [row for row in rows if row["split"] == "train"]
    calibration = [row for row in rows if row["split"] == "validation"]
    labels = sorted(
        {
            label
            for row in train
            for label in json.loads(row["assistant_response"])["secondary_intents"]
        }
    )
    model = TfidfIntentBaseline()
    model.fit(train, labels)

    thresholds = calibrate_secondary_thresholds(model, calibration, labels)

    assert set(thresholds) == set(labels)
    assert all(0 < value < 1 for value in thresholds.values())


def test_evaluation_reports_isolated_calibration_as_threshold_source():
    rows = [row.to_dict() for row in build_multilabel_intent_examples(train_per_family=3)]
    development = [
        {
            "case_id": "calibration-contract-case",
            "user_message": "复盘这周并判断是否加重量",
            "expected_primary_intent": "weekly_review",
            "required_secondary_intents": ["progression_decision"],
        }
    ]

    report = evaluate_baseline(rows, development, 0.5, rows)

    assert report["configuration"]["threshold_source"] == "isolated_calibration"
    assert report["configuration"]["per_label_thresholds"]
    assert report["dataset"]["calibration_rows"] > 0
    assert report["calibration_secondary"] is not None
