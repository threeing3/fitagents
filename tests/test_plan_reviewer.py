"""Tests for periodic plan review service."""

import uuid
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


class TestPlanReviewer:
    def test_init(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer
        mock_db = MagicMock()
        reviewer = PlanReviewer(mock_db)
        assert reviewer.db is mock_db
        assert reviewer.model_provider is not None

    def test_compute_stats_empty(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer
        mock_db = MagicMock()
        reviewer = PlanReviewer(mock_db)
        stats = reviewer._compute_stats([], [], [], [], 7)
        assert stats["period_days"] == 7
        assert stats["total_workouts"] == 0
        assert stats["adherence_pct"] == 0
        assert stats["risk_signals"]["high_soreness_days"] == 0

    def test_compute_stats_with_data(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer
        from fast_api.app.db import models

        mock_db = MagicMock()
        reviewer = PlanReviewer(mock_db)

        # Create mock workouts
        w1 = MagicMock(spec=models.WorkoutLog)
        w1.duration_minutes = 45
        w1.rpe = 7
        w1.completion_rate = 0.9
        w2 = MagicMock(spec=models.WorkoutLog)
        w2.duration_minutes = 30
        w2.rpe = 6
        w2.completion_rate = 0.8

        # Create mock check-ins
        c1 = MagicMock(spec=models.DailyCheckin)
        c1.sleep_hours = 7.5
        c1.energy_level = 4
        c1.soreness_level = 2
        c2 = MagicMock(spec=models.DailyCheckin)
        c2.sleep_hours = 6.0
        c2.energy_level = 3
        c2.soreness_level = 4  # high soreness

        # Create mock body metrics
        b1 = MagicMock(spec=models.BodyMetric)
        b1.weight_kg = 75.0
        b2 = MagicMock(spec=models.BodyMetric)
        b2.weight_kg = 74.2

        stats = reviewer._compute_stats(
            [w1, w2], [c1, c2], [b1, b2], [], 7,
        )

        assert stats["total_workouts"] == 2
        assert stats["total_duration_minutes"] == 75
        assert stats["avg_rpe"] == 6.5
        assert stats["avg_completion_pct"] == 0.85
        assert stats["avg_sleep_hours"] == 6.75
        assert stats["avg_energy"] == 3.5
        assert stats["weight_change_kg"] == -0.8
        assert stats["risk_signals"]["high_soreness_days"] == 1

    def test_compute_adherence(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer

        mock_db = MagicMock()
        reviewer = PlanReviewer(mock_db)
        stats = reviewer._compute_stats([], [], [], [], 7)
        assert stats["adherence_pct"] == 0

        from fast_api.app.db import models
        # 7 workouts in 7 days = 100% adherence
        workouts = [MagicMock(spec=models.WorkoutLog, duration_minutes=30, rpe=5, completion_rate=1.0) for _ in range(7)]
        stats = reviewer._compute_stats(workouts, [], [], [], 7)
        assert stats["adherence_pct"] == 100.0

    def test_fallback_review_weekly(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer

        mock_db = MagicMock()
        reviewer = PlanReviewer(mock_db)
        stats = {
            "period_days": 7,
            "total_workouts": 5,
            "adherence_pct": 71.4,
            "avg_rpe": 7.0,
            "weight_change_kg": -0.5,
            "avg_sleep_hours": 7.0,
            "risk_signals": {"high_soreness_days": 1, "low_energy_days": 0, "poor_sleep_days": 0},
        }
        review = reviewer._fallback_review(stats, 7)
        assert "5 workouts" in review
        assert "week" in review
        assert "moderate" in review
        assert "RPE" in review

    def test_fallback_review_monthly(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer

        mock_db = MagicMock()
        reviewer = PlanReviewer(mock_db)
        stats = {
            "period_days": 30,
            "total_workouts": 25,
            "adherence_pct": 83.3,
            "avg_rpe": 6.5,
            "weight_change_kg": None,
            "avg_sleep_hours": 0,
            "risk_signals": {"high_soreness_days": 0, "low_energy_days": 0, "poor_sleep_days": 0},
        }
        review = reviewer._fallback_review(stats, 30)
        assert "25 workouts" in review
        assert "month" in review
        assert "excellent" in review

    def test_fallback_review_low_adherence(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer

        mock_db = MagicMock()
        reviewer = PlanReviewer(mock_db)
        stats = {
            "period_days": 7,
            "total_workouts": 2,
            "adherence_pct": 28.6,
            "avg_rpe": 0,
            "weight_change_kg": None,
            "avg_sleep_hours": 0,
            "risk_signals": {"high_soreness_days": 0, "low_energy_days": 0, "poor_sleep_days": 0},
        }
        review = reviewer._fallback_review(stats, 7)
        assert "lower than ideal" in review

    def test_fallback_review_high_soreness(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer

        mock_db = MagicMock()
        reviewer = PlanReviewer(mock_db)
        stats = {
            "period_days": 7,
            "total_workouts": 4,
            "adherence_pct": 57.1,
            "avg_rpe": 0,
            "weight_change_kg": None,
            "avg_sleep_hours": 0,
            "risk_signals": {"high_soreness_days": 3, "low_energy_days": 0, "poor_sleep_days": 0},
        }
        review = reviewer._fallback_review(stats, 7)
        assert "recovery" in review.lower()
        assert "soreness" in review.lower()

    def test_review_structure(self):
        """Test that review() returns the expected structure."""
        from fast_api.app.services.plan_reviewer import PlanReviewer
        from fast_api.app.db import models

        mock_db = MagicMock()
        mock_provider = MagicMock()
        mock_provider.has_live_model.return_value = False

        # Mock user
        mock_user = MagicMock(spec=models.User)
        mock_user.id = uuid.uuid4()
        mock_db.get.return_value = mock_user

        # Mock queries return empty lists
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []
        mock_query.first.return_value = None
        mock_db.query.return_value = mock_query

        reviewer = PlanReviewer(mock_db, mock_provider)
        result = reviewer.review(mock_user.id, period_days=7)

        assert "user_id" in result
        assert "period_days" in result
        assert "period_start" in result
        assert "period_end" in result
        assert "stats" in result
        assert "review" in result
        assert "review_type" in result
        assert "generated_at" in result
        assert result["review_type"] == "rule_based"

    def test_review_nonexistent_user(self):
        from fast_api.app.services.plan_reviewer import PlanReviewer

        mock_db = MagicMock()
        mock_db.get.return_value = None

        reviewer = PlanReviewer(mock_db)
        with pytest.raises(ValueError, match="not found"):
            reviewer.review(uuid.uuid4())

    def test_endpoint_exists_in_router(self):
        """Verify the review endpoint is registered."""
        with open("fast_api/app/api/coach_platform.py") as f:
            content = f.read()
        assert 'def review_plan' in content
        assert 'PlanReviewer' in content
        assert '"/plans/review"' in content
