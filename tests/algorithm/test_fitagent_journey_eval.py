from algorithm.evaluation.fitagent_journey_eval import (
    CHECK_NAMES,
    evaluate_journeys,
    load_journey_dataset,
)


def test_fixed_journey_dataset_is_synthetic_and_not_training_eligible():
    dataset = load_journey_dataset()

    assert dataset["source"] == "synthetic_fixed_journey_v1"
    assert dataset["training_eligible"] is False
    assert len(dataset["cases"]) == 8
    assert len({case["case_id"] for case in dataset["cases"]}) == 8
    assert {case["correction_type"] for case in dataset["cases"]} == {
        "injury_clear",
        "goal_replace",
    }


def test_fixed_journeys_complete_every_declared_task_check():
    report = evaluate_journeys()

    assert report["dataset"]["independent_unit"] == "isolated_synthetic_user_journey"
    assert report["schema_version"] == "fitagent-journey-eval/v2"
    assert report["execution_protocol"] == "explicit_delegation_dated_proposal_approval_v2"
    assert report["summary"]["cases"] == 8
    assert report["summary"]["passed"] == 8
    assert report["summary"]["task_success_rate"] == 1.0
    assert report["summary"]["check_rates"] == {name: 1.0 for name in CHECK_NAMES}
    assert all(case["passed"] for case in report["cases"])
