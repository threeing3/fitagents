"""Full-record accounting, provenance preservation, and explicit safety overflow."""

from copy import deepcopy

from fast_api.app.services.context_window_manager import ContextWindowManager, estimate_dict_tokens


def test_short_summary_cannot_hide_long_content():
    manager = ContextWindowManager()
    original = {
        "id": "long",
        "summary": "短摘要",
        "content": "合成原文 " * 10000,
        "evidence": [{"table": "synthetic", "id": "source-1"}],
    }
    before = deepcopy(original)
    manager.set_memories([original])
    budget = manager.budgets["memory"]
    assert budget.truncated
    assert budget.items
    assert budget.used_tokens == estimate_dict_tokens(budget.items)
    assert budget.used_tokens <= budget.max_tokens
    assert budget.items[0]["evidence"] == original["evidence"]
    assert budget.items[0]["content"] != original["content"]
    assert budget.items[0]["_context_text_truncated"]
    assert original == before


def test_metadata_is_counted_and_never_silently_rewritten():
    manager = ContextWindowManager()
    memory = {"id": "metadata", "summary": "brief", "metadata": {"source": "x" * 20000}}
    manager.set_memories([memory])
    budget = manager.budgets["memory"]
    assert budget.truncated
    assert budget.items == []
    assert budget.used_tokens == estimate_dict_tokens([])
    assert manager.stats()["memory_budget_diagnostics"]["unfit_ids"] == ["metadata"]


def test_low_importance_risk_is_not_displaced_or_text_clipped():
    manager = ContextWindowManager()
    ordinary = {"id": "ordinary", "summary": "x" * 4000, "importance": 1.0}
    risk = {
        "id": "risk",
        "category": "risk",
        "summary": "risk",
        "content": "合成风险约束：不得继续原训练。" * 1000,
        "importance": 0.01,
    }
    manager.set_memories([ordinary, risk])
    budget = manager.budgets["memory"]
    assert budget.items == [risk]
    assert budget.used_tokens == estimate_dict_tokens([risk])
    assert manager.stats()["budgets"]["memory"]["over_budget"]
    assert manager.stats()["memory_budget_diagnostics"]["protected_overflow_ids"] == ["risk"]


def test_full_record_accounting_resets_when_reused_with_empty_input():
    manager = ContextWindowManager()
    manager.set_memories([{"id": "long", "summary": "x" * 30000}])
    manager.set_memories([])
    budget = manager.budgets["memory"]
    assert budget.items == []
    assert not budget.truncated
    assert budget.used_tokens == estimate_dict_tokens([])
    assert manager.stats()["memory_budget_diagnostics"] == {
        "unfit_ids": [],
        "protected_overflow_ids": [],
    }
