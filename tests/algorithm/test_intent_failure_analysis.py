import pytest

from algorithm.evaluation.intent_failure_analysis import analyze_failures


def _row(case_id: str, category: str, *, exact: bool, failed: str | None = None) -> dict:
    checks = {name: True for name in ("primary_intent", "secondary_intents", "risk_level", "clarification")}
    if failed:
        checks[failed] = False
    return {"case_id": case_id, "category": category, "exact_pass": exact, "checks": checks, "user_message": "must not leak"}


def test_failure_analysis_aligns_paths_and_removes_prompts():
    report = analyze_failures(
        {
            "rule_only": [_row("a", "multi_intent", exact=False, failed="secondary_intents"), _row("b", "multi_intent", exact=True)],
            "deepseek_all": [_row("a", "multi_intent", exact=True), _row("b", "multi_intent", exact=False, failed="clarification")],
            "hybrid": [_row("a", "multi_intent", exact=True), _row("b", "multi_intent", exact=True)],
        }
    )

    assert report["cases"] == 2
    assert report["contains_user_messages"] is False
    assert report["transitions"]["deepseek_all"] == {"rescued_from_rule": 1, "regressed_from_rule": 1}
    assert report["categories"][0]["best_observed_paths"] == ["hybrid"]
    assert "must not leak" not in str(report)


def test_failure_analysis_rejects_misaligned_case_ids():
    with pytest.raises(ValueError, match="same non-empty case_id"):
        analyze_failures(
            {
                "rule_only": [_row("a", "x", exact=True)],
                "deepseek_all": [_row("b", "x", exact=True)],
                "hybrid": [_row("a", "x", exact=True)],
            }
        )
