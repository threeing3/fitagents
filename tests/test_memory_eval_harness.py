import json
from pathlib import Path


REQUIRED_CASE_FIELDS = {
    "user_message",
    "seeded_memories",
    "seeded_logs",
    "expected_recalled_terms",
    "expected_intent",
    "expected_safety_rule",
    "should_not_include",
}


def test_hindsight_memory_eval_cases_have_required_fields():
    path = Path("tests/evals/hindsight_memory_eval_cases.json")
    cases = json.loads(path.read_text(encoding="utf-8"))

    assert cases
    for case in cases:
        assert REQUIRED_CASE_FIELDS <= set(case)
        assert isinstance(case["seeded_memories"], list)
        assert isinstance(case["seeded_logs"], dict)
        assert isinstance(case["expected_recalled_terms"], list)
        assert isinstance(case["should_not_include"], list)


def test_memory_eval_scripts_exist():
    assert Path("scripts/run-memory-evals.ps1").exists()
    assert Path("scripts/run_memory_evals.py").exists()
