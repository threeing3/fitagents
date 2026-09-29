from algorithm.evaluation.fitagent_evaluation_challenge import (
    ALLOWED_SCENARIOS,
    evaluate_challenges,
    load_challenge_dataset,
)


def test_evaluation_challenge_dataset_is_fixed_synthetic_and_complete():
    dataset = load_challenge_dataset()

    assert dataset["source"] == "synthetic_fixed_evaluation_challenge_v1"
    assert dataset["training_eligible"] is False
    assert len(dataset["cases"]) == 8
    assert {case["scenario"] for case in dataset["cases"]} == ALLOWED_SCENARIOS


def test_all_evaluation_challenges_reach_their_declared_safe_state():
    report = evaluate_challenges()

    assert report["summary"]["cases"] == 8
    assert report["summary"]["passed"] == 8
    assert report["summary"]["failed"] == 0
    assert report["summary"]["task_success_rate"] == 1.0
    assert all(rate == 1.0 for rate in report["summary"]["scenario_success_rates"].values())
