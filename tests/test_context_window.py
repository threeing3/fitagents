"""Tests for context window management and token-aware compaction."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestTokenEstimation:
    def test_empty_string(self):
        from fast_api.app.services.context_window_manager import estimate_tokens
        assert estimate_tokens("") == 0

    def test_short_english(self):
        from fast_api.app.services.context_window_manager import estimate_tokens
        tokens = estimate_tokens("Hello world")
        assert tokens >= 1
        assert tokens <= 10  # Very short, should be ~3 tokens

    def test_chinese_text(self):
        from fast_api.app.services.context_window_manager import estimate_tokens
        text = "你好世界这是一段中文文本用来测试token估算"
        tokens = estimate_tokens(text)
        assert tokens >= 1
        # Chinese is denser (~1.5 chars/token) but our estimate uses 3.5
        # So it will overestimate (safer)

    def test_long_text(self):
        from fast_api.app.services.context_window_manager import estimate_tokens
        text = "a" * 3500
        tokens = estimate_tokens(text)
        assert tokens == 1000  # 3500 / 3.5 = 1000

    def test_dict_estimation(self):
        from fast_api.app.services.context_window_manager import estimate_dict_tokens
        data = {"key": "value", "nested": {"a": 1, "b": [1, 2, 3]}}
        tokens = estimate_dict_tokens(data)
        assert tokens > 0


class TestContextWindowManager:
    def test_init_default(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager()
        assert mgr.model_name == "unknown"
        assert mgr.total_tokens == 8000  # Default for unknown

    def test_init_gpt4o(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-4o")
        assert mgr.total_tokens == 120_000

    def test_init_claude(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="claude-sonnet-4-20250514")
        assert mgr.total_tokens == 180_000

    def test_budgets_initialized(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-4o")
        assert "system" in mgr.budgets
        assert "profile" in mgr.budgets
        assert "plan" in mgr.budgets
        assert "risk" in mgr.budgets
        assert "memory" in mgr.budgets
        assert "knowledge" in mgr.budgets
        assert "history" in mgr.budgets
        # All should have positive max_tokens
        for b in mgr.budgets.values():
            assert b.max_tokens > 0

    def test_set_system_prompt(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-4o")
        mgr.set_system_prompt("You are a helpful assistant.")
        assert mgr.budgets["system"].used_tokens > 0

    def test_set_profile(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager()
        profile = {"age": 30, "goal": "muscle_gain", "weight_kg": 75.0}
        mgr.set_profile(profile)
        assert mgr.budgets["profile"].used_tokens > 0

    def test_set_memories_fits(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-4o")
        memories = [
            {"summary": "User prefers morning workouts", "importance": 0.8},
            {"summary": "Has knee injury history", "importance": 0.9},
            {"summary": "Likes compound exercises", "importance": 0.7},
        ]
        mgr.set_memories(memories)
        assert len(mgr.budgets["memory"].items) == 3
        assert not mgr.budgets["memory"].truncated

    def test_set_memories_truncates(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-3.5-turbo")  # 4000 tokens, memory budget 800
        # Create many long memories to force truncation
        memories = [
            {"summary": "x" * 500, "importance": i / 100}
            for i in range(50)
        ]
        mgr.set_memories(memories)
        # Should have truncated
        assert mgr.budgets["memory"].truncated
        assert len(mgr.budgets["memory"].items) < 50

    def test_memories_sorted_by_importance(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-3.5-turbo")
        memories = [
            {"summary": "low value", "importance": 0.1},
            {"summary": "critical safety info", "importance": 1.0},
            {"summary": "medium value", "importance": 0.5},
        ]
        mgr.set_memories(memories)
        items = mgr.budgets["memory"].items
        if len(items) == 1:
            # Only one fits — should be the highest importance
            assert items[0]["importance"] == 1.0

    def test_history_keeps_recent(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-3.5-turbo")
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "What should I eat?"},
            {"role": "assistant", "content": "Eat protein and vegetables."},
            {"role": "user", "content": "a" * 2000},  # Large message
        ]
        mgr.set_history(history)
        # Due to truncation, the large message might be dropped
        items = mgr.budgets["history"].items
        assert len(items) > 0

    def test_build_returns_string(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-4o")
        mgr.set_system_prompt("You are a fitness coach.")
        mgr.set_profile({"age": 30, "goal": "fat_loss"})
        result = mgr.build()
        assert isinstance(result, str)
        assert "Context Budget" in result
        assert "fitness coach" in result

    def test_build_includes_all_sections(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-4o")
        mgr.set_system_prompt("System prompt")
        mgr.set_profile({"goal": "strength"})
        mgr.set_plan({"training_days": [{"day": "Monday"}]})
        mgr.set_risk_notes([{"body_part": "knee", "severity": "high"}])
        mgr.set_memories([{"summary": "Memory", "importance": 0.8}])
        mgr.set_knowledge({"decision_rules": []})
        mgr.set_history([{"role": "user", "content": "Hello"}])

        result = mgr.build()
        assert "System prompt" in result
        assert "strength" in result
        assert "Monday" in result
        assert "knee" in result
        assert "Memory" in result
        assert "Hello" in result

    def test_should_compact(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-3.5-turbo")  # Small window
        # Should not compact initially
        assert not mgr.should_compact()

        # Fill it up
        mgr.set_system_prompt("x" * 10000)
        assert mgr.should_compact()

    def test_stats(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-4o")
        stats = mgr.stats()
        assert "model_name" in stats
        assert "total_window_tokens" in stats
        assert "budgets" in stats
        assert "total_used" in stats
        assert "should_compact" in stats

    def test_risk_notes_never_trimmed(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-4o")
        mgr.set_risk_notes([{"body_part": "spine", "severity": "critical"}])
        assert mgr.budgets["risk"].used_tokens > 0
        # Risk notes should always be included in build
        result = mgr.build()
        assert "spine" in result

    def test_compaction_stats_tracked(self):
        from fast_api.app.services.context_window_manager import ContextWindowManager
        mgr = ContextWindowManager(model_name="gpt-3.5-turbo")
        memories = [{"summary": "x" * 500, "importance": 0.1} for _ in range(100)]
        mgr.set_memories(memories)
        stats = mgr.stats()
        if mgr.budgets["memory"].truncated:
            assert mgr.total_trimmed_items > 0
            assert mgr.compaction_count > 0


class TestBuildContextPacketWithBudget:
    def test_wraps_existing_packet(self):
        from fast_api.app.services.context_window_manager import build_context_packet_with_budget

        packet = {
            "intent": "training_plan",
            "core_profile": {"age": 30, "goal": "strength"},
            "active_plan": {"training_days": []},
            "active_risk_notes": [],
            "relevant_memories": [{"summary": "test", "importance": 0.8}],
            "knowledge_context": {"decision_rules": []},
            "current_request_policy": {},
        }
        compacted, stats = build_context_packet_with_budget(
            packet,
            model_name="gpt-4o",
            system_prompt="You are a coach.",
        )
        assert "intent" in compacted
        assert stats["model_name"] == "gpt-4o"

    def test_memory_truncation_flag(self):
        from fast_api.app.services.context_window_manager import build_context_packet_with_budget

        huge_memories = [
            {"summary": "x" * 1000, "importance": 0.1}
            for _ in range(100)
        ]
        packet = {
            "intent": "general_chat",
            "core_profile": {},
            "active_plan": {},
            "active_risk_notes": [],
            "relevant_memories": huge_memories,
            "knowledge_context": {},
            "current_request_policy": {},
        }
        compacted, stats = build_context_packet_with_budget(
            packet, model_name="gpt-3.5-turbo"
        )
        if stats["budgets"]["memory"]["truncated"]:
            assert "_memory_truncated" in compacted


class TestTokenBudget:
    def test_remaining(self):
        from fast_api.app.services.context_window_manager import TokenBudget
        budget = TokenBudget("test", max_tokens=1000, used_tokens=300)
        assert budget.remaining == 700

    def test_usage_pct(self):
        from fast_api.app.services.context_window_manager import TokenBudget
        budget = TokenBudget("test", max_tokens=1000, used_tokens=250)
        assert budget.usage_pct == 25.0

    def test_usage_pct_zero_max(self):
        from fast_api.app.services.context_window_manager import TokenBudget
        budget = TokenBudget("test", max_tokens=0, used_tokens=0)
        assert budget.usage_pct == 0.0
