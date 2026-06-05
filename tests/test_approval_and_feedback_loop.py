"""Tests for approval system and feedback learner."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# Approval Manager Tests
# ============================================================

class TestApprovalManager:
    def test_requires_approval_read_tool(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        assert not mgr.requires_approval("context.build", "read", False)

    def test_requires_approval_write_tool(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        assert mgr.requires_approval("memory.write", "write", True)

    def test_requires_approval_write_candidate(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        # write_candidate without side_effects — still needs approval since it feeds write ops
        assert mgr.requires_approval("profile.extract", "write_candidate", False)

    def test_create_approval(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        user_id = uuid.uuid4()
        approval = mgr.create_approval(
            user_id=user_id,
            session_id=uuid.uuid4(),
            tool_name="plan.generate",
            tool_description="Generate training plan",
            permission_level="write",
            input_summary={"reason": "user requested plan"},
        )
        assert approval.status == "pending"
        assert approval.user_id == user_id
        assert approval.tool_name == "plan.generate"

    def test_approve_resolves_pending(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        approval = mgr.create_approval(
            user_id=uuid.uuid4(),
            session_id=None,
            tool_name="memory.write",
            tool_description="Save memories",
            permission_level="write",
            input_summary={},
        )
        result = mgr.approve(approval.approval_id)
        assert result is not None
        assert result.status == "approved"

    def test_deny_resolves_pending(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        approval = mgr.create_approval(
            user_id=uuid.uuid4(),
            session_id=None,
            tool_name="memory.write",
            tool_description="Save memories",
            permission_level="write",
            input_summary={},
        )
        result = mgr.deny(approval.approval_id, reason="Not needed")
        assert result is not None
        assert result.status == "denied"

    def test_cannot_approve_twice(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        approval = mgr.create_approval(
            user_id=uuid.uuid4(),
            session_id=None,
            tool_name="plan.generate",
            tool_description="Generate plan",
            permission_level="write",
            input_summary={},
        )
        mgr.approve(approval.approval_id)
        result2 = mgr.approve(approval.approval_id)
        assert result2 is None

    def test_get_pending(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        user_id = uuid.uuid4()
        mgr.create_approval(user_id, None, "tool1", "d", "write", {})
        mgr.create_approval(user_id, None, "tool2", "d", "write", {})
        pending = mgr.get_pending(user_id)
        assert len(pending) == 2

    def test_get_pending_excludes_decided(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mgr = ApprovalManager(MagicMock())
        user_id = uuid.uuid4()
        a1 = mgr.create_approval(user_id, None, "tool1", "d", "write", {})
        a2 = mgr.create_approval(user_id, None, "tool2", "d", "write", {})
        mgr.approve(a1.approval_id)
        pending = mgr.get_pending(user_id)
        assert len(pending) == 1

    def test_check_auto_approve_insufficient_history(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 1  # Only 1 past approval
        mock_db.query.return_value = mock_query

        mgr = ApprovalManager(mock_db)
        assert not mgr.check_auto_approve(uuid.uuid4(), "plan.generate", "training_plan")

    def test_check_auto_approve_sufficient_history(self):
        from fast_api.app.services.approval_manager import ApprovalManager
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 5  # 5 past approvals
        mock_db.query.return_value = mock_query

        mgr = ApprovalManager(mock_db)
        assert mgr.check_auto_approve(uuid.uuid4(), "plan.generate", "training_plan")

    def test_max_pending_enforced(self):
        from fast_api.app.services.approval_manager import (
            ApprovalManager,
            MAX_PENDING_PER_USER,
        )
        mgr = ApprovalManager(MagicMock())
        user_id = uuid.uuid4()
        for i in range(MAX_PENDING_PER_USER + 2):
            mgr.create_approval(user_id, None, f"tool{i}", "d", "write", {})
        pending = mgr.get_pending(user_id)
        assert len(pending) <= MAX_PENDING_PER_USER


class TestSummarizeTool:
    def test_known_tool_has_description(self):
        from fast_api.app.services.approval_manager import summarize_tool_for_approval
        summary = summarize_tool_for_approval("plan.generate", {"reason": "test"})
        assert "plan" in summary["description"].lower()
        assert summary["tool_name"] == "plan.generate"

    def test_unknown_tool_fallback(self):
        from fast_api.app.services.approval_manager import summarize_tool_for_approval
        summary = summarize_tool_for_approval("unknown.tool", {})
        assert "Execute tool" in summary["description"]

    def test_long_input_truncated(self):
        from fast_api.app.services.approval_manager import summarize_tool_for_approval
        summary = summarize_tool_for_approval("plan.generate", {
            "reason": "x" * 1000
        })
        preview = summary["input_preview"]
        assert len(str(preview.get("reason", ""))) < 500


# ============================================================
# Feedback Learner Tests
# ============================================================

class TestFeedbackCollector:
    def test_get_recent_feedback(self):
        from fast_api.app.services.feedback_learner import FeedbackCollector
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []
        mock_db.query.return_value = mock_query

        collector = FeedbackCollector(mock_db)
        result = collector.get_recent_feedback(uuid.uuid4())
        assert isinstance(result, list)

    def test_get_low_rated(self):
        from fast_api.app.services.feedback_learner import FeedbackCollector
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []
        mock_db.query.return_value = mock_query

        collector = FeedbackCollector(mock_db)
        result = collector.get_low_rated(uuid.uuid4())
        assert isinstance(result, list)

    def test_get_top_categories(self):
        from fast_api.app.services.feedback_learner import FeedbackCollector
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.group_by.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [MagicMock(category="too_generic", count=5)]
        mock_db.query.return_value = mock_query

        collector = FeedbackCollector(mock_db)
        result = collector.get_top_categories(uuid.uuid4())
        assert len(result) >= 1
        assert result[0]["category"] == "too_generic"


class TestPreferenceLearner:
    def test_insufficient_data(self):
        from fast_api.app.services.feedback_learner import (
            PreferenceLearner, FeedbackCollector
        )
        mock_db = MagicMock()
        collector = FeedbackCollector(mock_db)
        collector.get_low_rated = MagicMock(return_value=[])
        collector.get_top_categories = MagicMock(return_value=[])
        collector.get_rating_trend = MagicMock(return_value={
            "trend": [], "overall_avg": 0
        })

        learner = PreferenceLearner(collector)
        result = learner.learn(uuid.uuid4())
        assert result["confidence"] == 0.0
        assert result["behavioral_guidance"] == []

    def test_sufficient_data_produces_guidance(self):
        from fast_api.app.services.feedback_learner import (
            PreferenceLearner, FeedbackCollector
        )
        mock_db = MagicMock()
        collector = FeedbackCollector(mock_db)
        # Simulate enough negative feedback
        collector.get_low_rated = MagicMock(return_value=[
            {"rating": 2, "category": "too_generic"} for _ in range(4)
        ])
        collector.get_top_categories = MagicMock(return_value=[
            {"category": "too_generic", "count": 4},
        ])
        collector.get_rating_trend = MagicMock(return_value={
            "trend": [], "overall_avg": 2.5
        })

        learner = PreferenceLearner(collector)
        result = learner.learn(uuid.uuid4())
        assert result["confidence"] > 0
        assert len(result["behavioral_guidance"]) > 0
        assert any("specific" in g.lower() for g in result["behavioral_guidance"])

    def test_declining_trend_adds_guidance(self):
        from fast_api.app.services.feedback_learner import (
            PreferenceLearner, FeedbackCollector
        )
        mock_db = MagicMock()
        collector = FeedbackCollector(mock_db)
        collector.get_low_rated = MagicMock(return_value=[
            {"rating": 1, "category": "not_helpful"} for _ in range(5)
        ])
        collector.get_top_categories = MagicMock(return_value=[
            {"category": "not_helpful", "count": 5},
        ])
        collector.get_rating_trend = MagicMock(return_value={
            "trend": [{"date": "2026-01-01", "avg_rating": 2.0} for _ in range(5)],
            "overall_avg": 2.0,
        })

        learner = PreferenceLearner(collector)
        result = learner.learn(uuid.uuid4())
        # Should include both category guidance and declining trend warning
        assert len(result["behavioral_guidance"]) >= 2


class TestPromptEnhancer:
    def test_no_patterns_returns_base(self):
        from fast_api.app.services.feedback_learner import (
            PromptEnhancer, PreferenceLearner, FeedbackCollector
        )
        mock_db = MagicMock()
        collector = FeedbackCollector(mock_db)
        collector.get_low_rated = MagicMock(return_value=[])
        collector.get_top_categories = MagicMock(return_value=[])
        collector.get_rating_trend = MagicMock(return_value={
            "trend": [], "overall_avg": 0
        })
        learner = PreferenceLearner(collector)
        enhancer = PromptEnhancer(learner)

        base = "You are a fitness coach."
        enhanced, debug = enhancer.enhance_system_prompt(uuid.uuid4(), base)
        assert enhanced == base
        assert not debug["enhanced"]

    def test_patterns_add_learned_section(self):
        from fast_api.app.services.feedback_learner import (
            PromptEnhancer, PreferenceLearner, FeedbackCollector
        )
        mock_db = MagicMock()
        collector = FeedbackCollector(mock_db)
        collector.get_low_rated = MagicMock(return_value=[
            {"rating": 1, "category": "too_generic"} for _ in range(4)
        ])
        collector.get_top_categories = MagicMock(return_value=[
            {"category": "too_generic", "count": 4},
        ])
        collector.get_rating_trend = MagicMock(return_value={
            "trend": [], "overall_avg": 3.5
        })
        learner = PreferenceLearner(collector)
        enhancer = PromptEnhancer(learner)

        base = "You are a fitness coach."
        enhanced, debug = enhancer.enhance_system_prompt(uuid.uuid4(), base)
        assert len(enhanced) > len(base)
        assert "Learned Preferences" in enhanced
        assert debug["enhanced"]
        assert debug["confidence"] > 0


class TestCorrectionCategories:
    def test_all_categories_have_guidance(self):
        from fast_api.app.services.feedback_learner import CORRECTION_CATEGORIES
        assert len(CORRECTION_CATEGORIES) >= 10
        for guidance in CORRECTION_CATEGORIES.values():
            assert len(guidance) > 20  # Must be meaningful


class TestBuildAdaptivePrompt:
    def test_returns_base_and_debug(self):
        from fast_api.app.services.feedback_learner import build_adaptive_system_prompt

        mock_db = MagicMock()
        # Mock feedback queries to return empty
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []
        mock_query.scalar.return_value = 0
        mock_db.query.return_value = mock_query

        def mock_get(prompt_id):
            return f"Base prompt for {prompt_id}"

        prompt, debug = build_adaptive_system_prompt(
            mock_db, uuid.uuid4(), "coach_coaching_reply", mock_get
        )
        assert "Base prompt" in prompt
        assert isinstance(debug, dict)


# ============================================================
# Integration: approval route tests
# ============================================================

class TestApprovalAPI:
    def test_router_exists(self):
        from fast_api.app.api.approval_api import approval_router
        assert approval_router is not None
        assert approval_router.prefix == "/v1/approvals"

    def test_main_includes_approval_router(self):
        with open("fast_api/app/main.py") as f:
            content = f.read()
        assert "from fast_api.app.api.approval_api import approval_router" in content
        assert "app.include_router(approval_router)" in content
