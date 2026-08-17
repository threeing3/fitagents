import asyncio
from types import SimpleNamespace

from algorithm.evaluation.intent_live_eval import evaluate_rows


def test_rule_only_eval_never_requests_a_model(monkeypatch):
    class FailProvider:
        settings = SimpleNamespace(llm_provider="offline", chat_model="offline")

        def chat_model(self, temperature=0.0):
            raise AssertionError("rule_only must not request a model")

    monkeypatch.setattr("algorithm.evaluation.intent_live_eval.ModelProvider", FailProvider)
    rows = [
        {
            "name": "plan",
            "input": "帮我制定一周训练计划",
            "expected_primary_intent": "training_plan",
            "expected_secondary_intents": [],
            "expected_risk_level": "low",
            "expected_needs_clarification": True,
        }
    ]

    summary, details = asyncio.run(evaluate_rows(rows, "rule_only"))

    assert summary["cases"] == 1
    assert summary["model_calls"] == 0
    assert details[0]["actual"]["primary_intent"] == "training_plan"
    assert "input" not in details[0]
