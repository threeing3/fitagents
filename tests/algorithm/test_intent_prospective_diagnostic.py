"""Guard the frozen, assistant-authored intent diagnostic cases."""

import json
from collections import Counter
from pathlib import Path

from algorithm.evaluation.intent_eval_core import RISK_ORDER
from algorithm.inference.intent_catalog import AgentIntentCatalog

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "algorithm/datasets/fixtures/intent_prospective_diagnostic_v1.json"


def _load_cases() -> list[dict]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def test_prospective_cases_have_balanced_stable_families() -> None:
    cases = _load_cases()
    assert len(cases) == 32
    assert [row["case_id"] for row in cases] == [
        f"intent-prosp-{number:03d}" for number in range(1, 33)
    ]
    assert set(Counter(row["family"] for row in cases).values()) == {4}
    assert len({row["family"] for row in cases}) == 8


def test_prospective_labels_follow_the_intent_contract() -> None:
    for row in _load_cases():
        assert row["user_message"].strip()
        assert row["rationale"].strip()
        assert row["training_eligible"] is False
        assert row["expected_primary_intent"] in AgentIntentCatalog.VALID_INTENTS
        secondary = row["expected_secondary_intents"]
        assert isinstance(secondary, list)
        assert len(secondary) == len(set(secondary))
        assert all(intent in AgentIntentCatalog.VALID_INTENTS for intent in secondary)
        assert row["expected_primary_intent"] not in secondary
        assert row["expected_risk_level"] in RISK_ORDER
        assert isinstance(row["expected_clarification"], bool)


def test_prospective_messages_do_not_exactly_reuse_old_cases_or_training_candidates() -> None:
    previous: set[str] = set()
    for relative_path in (
        "tests/evals/agent_challenge_cases.json",
        "tests/evals/intent_eval_cases.json",
        "algorithm/datasets/development/intent_semantic_probe_v1.json",
        "algorithm/datasets/development/intent_semantic_probe_v2.json",
        "algorithm/datasets/development/intent_semantic_probe_v3.json",
    ):
        payload = json.loads((ROOT / relative_path).read_text(encoding="utf-8"))
        rows = payload["cases"] if isinstance(payload, dict) else payload
        previous.update(str(row.get("user_message") or row["input"]).strip() for row in rows)
    training_path = ROOT / "algorithm/datasets/generated/intent_multilabel_v3_1_20260922.jsonl"
    if training_path.exists():
        previous.update(
            json.loads(line)["user_message"].strip()
            for line in training_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    cases = _load_cases()
    messages = [row["user_message"].strip() for row in cases]
    assert len(messages) == len(set(messages))
    assert not set(messages).intersection(previous)
