from algorithm.evaluation.build_intent_development_set import build_cases, validate_isolation


def test_development_set_has_required_categories_and_is_not_training_data():
    rows = build_cases()

    assert len(rows) == 90
    assert {row["category"] for row in rows} == {
        "multi_intent",
        "missing_parameters",
        "safety_bypass",
    }
    assert all(row["partition"] == "development" for row in rows)
    assert all(row["training_eligible"] is False for row in rows)
    assert all(row["human_review_status"] == "not_reviewed" for row in rows)


def test_isolation_gate_rejects_copied_test_prompt():
    development = [{"user_message": "完全相同的固定问题"}]
    frozen = [{"user_message": "完全相同的固定问题"}]

    report = validate_isolation(development, frozen)

    assert report["passed"] is False
    assert report["normalized_exact_overlap"] == 1
