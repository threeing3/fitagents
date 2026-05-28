"""Periodic plan review service — generates weekly and monthly progress reports.

Analyzes workout logs, daily check-ins, body metrics, and recovery data
to produce a comprehensive review with LLM-generated insights and recommendations.
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from fast_api.app.core.metrics import plans_generated_total
from fast_api.app.core.prompts import registry
from fast_api.app.db import models
from fast_api.app.services.model_provider import ModelProvider

logger = logging.getLogger(__name__)

# Default review period in days
WEEKLY_DAYS = 7
MONTHLY_DAYS = 30


class PlanReviewer:
    """Generates periodic review reports for training plan progress."""

    def __init__(self, db: Session, model_provider: ModelProvider | None = None):
        self.db = db
        self.model_provider = model_provider or ModelProvider()

    def review(
        self,
        user_id: UUID,
        period_days: int = WEEKLY_DAYS,
        force_regenerate: bool = False,
    ) -> dict[str, Any]:
        """Generate a progress review for the specified period.

        Args:
            user_id: The user to review.
            period_days: Number of days to look back (7 = weekly, 30 = monthly).
            force_regenerate: If True, always generate fresh; otherwise reuse recent reviews.

        Returns:
            A dict with review summary, detailed sections, and recommendations.
        """
        since = date.today() - timedelta(days=period_days)
        user = self.db.get(models.User, user_id)
        if user is None:
            raise ValueError(f"User {user_id} not found")

        profile = self.db.get(models.UserProfile, user_id)

        # Collect data
        workouts = self._fetch_workouts(user_id, since)
        checkins = self._fetch_checkins(user_id, since)
        body_metrics = self._fetch_body_metrics(user_id, since)
        recovery = self._fetch_recovery(user_id, since)
        plan = self._fetch_active_plan(user_id)

        # Compute stats
        stats = self._compute_stats(workouts, checkins, body_metrics, recovery, period_days)

        # Generate LLM review if available
        review_text = None
        if self.model_provider.has_live_model():
            review_text = self._generate_llm_review(
                profile=profile,
                plan=plan,
                stats=stats,
                period_days=period_days,
            )

        plans_generated_total.inc()

        return {
            "user_id": user_id,
            "period_days": period_days,
            "period_start": since.isoformat(),
            "period_end": date.today().isoformat(),
            "stats": stats,
            "review": review_text or self._fallback_review(stats, period_days),
            "review_type": "llm" if review_text else "rule_based",
            "generated_at": datetime.utcnow().isoformat(),
        }

    # ----------------------------------------------------------------
    # Data collection
    # ----------------------------------------------------------------

    def _fetch_workouts(self, user_id: UUID, since: date) -> list[models.WorkoutLog]:
        return (
            self.db.query(models.WorkoutLog)
            .filter(
                models.WorkoutLog.user_id == user_id,
                models.WorkoutLog.performed_at >= since,
            )
            .order_by(models.WorkoutLog.performed_at.asc())
            .all()
        )

    def _fetch_checkins(self, user_id: UUID, since: date) -> list[models.DailyCheckin]:
        return (
            self.db.query(models.DailyCheckin)
            .filter(
                models.DailyCheckin.user_id == user_id,
                models.DailyCheckin.checkin_date >= since,
            )
            .order_by(models.DailyCheckin.checkin_date.asc())
            .all()
        )

    def _fetch_body_metrics(self, user_id: UUID, since: date) -> list[models.BodyMetric]:
        return (
            self.db.query(models.BodyMetric)
            .filter(
                models.BodyMetric.user_id == user_id,
                models.BodyMetric.created_at >= since,
            )
            .order_by(models.BodyMetric.created_at.asc())
            .all()
        )

    def _fetch_recovery(self, user_id: UUID, since: date) -> list[models.RecoveryLog]:
        return (
            self.db.query(models.RecoveryLog)
            .filter(
                models.RecoveryLog.user_id == user_id,
                models.RecoveryLog.created_at >= since,
            )
            .order_by(models.RecoveryLog.created_at.asc())
            .all()
        )

    def _fetch_active_plan(self, user_id: UUID) -> models.TrainingPlan | None:
        return (
            self.db.query(models.TrainingPlan)
            .filter(
                models.TrainingPlan.user_id == user_id,
                models.TrainingPlan.status == "active",
            )
            .order_by(desc(models.TrainingPlan.created_at))
            .first()
        )

    # ----------------------------------------------------------------
    # Stats computation
    # ----------------------------------------------------------------

    def _compute_stats(
        self,
        workouts: list[models.WorkoutLog],
        checkins: list[models.DailyCheckin],
        body_metrics: list[models.BodyMetric],
        recovery: list[models.RecoveryLog],
        period_days: int,
    ) -> dict[str, Any]:
        total_workouts = len(workouts)
        adherence = round(total_workouts / max(period_days, 1) * 100, 1)

        # Workout volume
        total_duration = sum(w.duration_minutes or 0 for w in workouts)
        avg_rpe = (
            round(sum(w.rpe or 0 for w in workouts) / max(total_workouts, 1), 1)
            if total_workouts
            else 0
        )
        avg_completion = (
            round(sum(w.completion_rate or 0 for w in workouts) / max(total_workouts, 1), 1)
            if total_workouts
            else 0
        )

        # Check-in trends
        avg_sleep = (
            round(sum(c.sleep_hours or 0 for c in checkins) / max(len(checkins), 1), 1)
            if checkins
            else 0
        )
        avg_energy = (
            round(sum(c.energy_level or 0 for c in checkins) / max(len(checkins), 1), 1)
            if checkins
            else 0
        )
        avg_soreness = (
            round(sum(c.soreness_level or 0 for c in checkins) / max(len(checkins), 1), 1)
            if checkins
            else 0
        )

        # Body metrics trend
        weight_change = None
        if len(body_metrics) >= 2:
            first_weight = body_metrics[0].weight_kg
            last_weight = body_metrics[-1].weight_kg
            if first_weight and last_weight:
                weight_change = round(last_weight - first_weight, 1)

        # Recovery
        recovery_count = len(recovery)
        avg_recovery = (
            round(sum(r.recovery_score or 0 for r in recovery) / max(recovery_count, 1), 1)
            if recovery_count
            else 0
        )

        # Risk signals from check-ins
        high_soreness_days = sum(1 for c in checkins if (c.soreness_level or 0) >= 4)
        low_energy_days = sum(1 for c in checkins if (c.energy_level or 0) <= 2)
        poor_sleep_days = sum(1 for c in checkins if (c.sleep_hours or 0) < 5)

        return {
            "period_days": period_days,
            "total_workouts": total_workouts,
            "adherence_pct": adherence,
            "total_duration_minutes": total_duration,
            "avg_rpe": avg_rpe,
            "avg_completion_pct": avg_completion,
            "avg_sleep_hours": avg_sleep,
            "avg_energy": avg_energy,
            "avg_soreness": avg_soreness,
            "weight_change_kg": weight_change,
            "recovery_logs": recovery_count,
            "avg_recovery_score": avg_recovery,
            "risk_signals": {
                "high_soreness_days": high_soreness_days,
                "low_energy_days": low_energy_days,
                "poor_sleep_days": poor_sleep_days,
            },
            "body_metric_count": len(body_metrics),
            "checkin_count": len(checkins),
        }

    # ----------------------------------------------------------------
    # Review generation
    # ----------------------------------------------------------------

    def _generate_llm_review(
        self,
        profile: models.UserProfile | None,
        plan: models.TrainingPlan | None,
        stats: dict[str, Any],
        period_days: int,
    ) -> str | None:
        """Use the LLM to generate a personalized review."""
        import asyncio

        period_label = "weekly" if period_days <= 7 else "monthly"

        system_prompt = (
            "You are a professional fitness coach writing a training progress review. "
            "Write in the user's language (Chinese if the profile suggests it, otherwise English). "
            "Be encouraging but honest. Structure your review with: "
            "1) a 2-3 sentence overview of the period, "
            "2) what went well, "
            "3) areas for improvement, "
            "4) a specific recommendation for the next period. "
            "Keep the total under 300 words. Be concise and direct."
        )

        profile_text = "No profile available."
        if profile:
            profile_text = (
                f"Age: {profile.age or 'N/A'}, "
                f"Goal: {profile.goal or 'N/A'}, "
                f"Experience: {profile.experience_level or 'N/A'}, "
                f"Equipment: {profile.equipment_available or []}"
            )

        plan_text = "No active training plan."
        if plan and isinstance(plan.plan_json, dict):
            plan_text = json.dumps(plan.plan_json, ensure_ascii=False, indent=2)[:500]

        user_prompt = (
            f"Generate a {period_label} progress review ({period_days} days).\n\n"
            f"User profile: {profile_text}\n\n"
            f"Current plan: {plan_text}\n\n"
            f"Stats for this period:\n{json.dumps(stats, indent=2)}\n\n"
            f"Write the {period_label} review now."
        )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            reply = loop.run_until_complete(
                self.model_provider.coach_reply(system_prompt, user_prompt)
            )
            return reply
        except Exception as exc:
            logger.warning("LLM review generation failed: %s", exc)
            return None

    def _fallback_review(self, stats: dict[str, Any], period_days: int) -> str:
        """Generate a rule-based review when LLM is unavailable."""
        period_label = "week" if period_days <= 7 else "month"
        adherence = stats.get("adherence_pct", 0)
        workouts = stats.get("total_workouts", 0)

        if adherence >= 80:
            adherence_msg = f"excellent training consistency this {period_label}"
        elif adherence >= 50:
            adherence_msg = f"moderate training consistency this {period_label}"
        else:
            adherence_msg = f"training frequency was lower than ideal this {period_label}"

        parts = [
            f"You completed {workouts} workouts over the past {period_days} days "
            f"({adherence_msg}).",
        ]

        if stats.get("avg_rpe"):
            parts.append(f"Average workout intensity (RPE) was {stats['avg_rpe']}/10.")
        if stats.get("weight_change_kg") is not None:
            direction = "lost" if stats["weight_change_kg"] < 0 else "gained"
            parts.append(
                f"You {direction} {abs(stats['weight_change_kg'])} kg over this period."
            )
        if stats.get("avg_sleep_hours"):
            parts.append(f"Average sleep was {stats['avg_sleep_hours']} hours per night.")

        risk = stats.get("risk_signals", {})
        if risk.get("high_soreness_days", 0) > 2:
            parts.append(
                "Consider adding more recovery — you reported high soreness on "
                f"{risk['high_soreness_days']} days."
            )

        return " ".join(parts)
