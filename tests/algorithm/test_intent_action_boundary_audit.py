from pathlib import Path

from algorithm.evaluation.intent_action_boundary_audit import audit_cases
from algorithm.evaluation.intent_semantic_probe import load_cases

ROOT = Path(__file__).resolve().parents[2]
PROBE_V2 = ROOT / "algorithm/datasets/development/intent_semantic_probe_v2.json"


def test_second_probe_has_distinct_synthetic_pairs():
    dataset, cases = load_cases(PROBE_V2)

    assert dataset["training_eligible"] is False
    assert dataset["human_review_status"] == "not_reviewed"
    assert len(cases) == 12
    assert len({row["case_id"] for row in cases}) == 12
    assert len({row["family"] for row in cases}) == 6


def test_action_audit_distinguishes_candidates_from_execution():
    _, cases = load_cases(PROBE_V2)

    details, summary = audit_cases(cases)

    assert len(details) == 12
    assert summary["cases"] == 12
    assert "No tool was executed" in summary["claim_boundary"]
    assert all("candidate_tools" in item for item in details)
    assert all("requested_actions" in item for item in details)
