"""
Feedback-driven prompt enhancement — closed loop from user ratings.

Mirrors how Claude Code learns: collect user feedback (ratings, categories,
comments), aggregate patterns over time, and inject learned preferences back
into the system prompt. This closes the loop between "user says it was bad"
and "agent adjusts behavior next time."

Architecture:
1. FeedbackCollector — ingests ratings and stores patterns
2. PreferenceLearner — aggregates feedback into actionable insights
3. PromptEnhancer — injects learned preferences into system prompts
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from fast_api.app.db import models

logger = logging.getLogger(__name__)

# Categories that signal specific correction needs
CORRECTION_CATEGORIES = {
    "too_generic": "Provide more specific, actionable advice with concrete numbers and examples.",
    "incorrect": "Double-check factual claims about exercises, nutrition, and training methodology.",
    "too_pushy": "Be supportive and encouraging, not demanding. Respect the user's pace.",
    "ignored_context": "Pay closer attention to the user's profile, past injuries, and stated preferences.",
    "language_issue": "Respond in the language the user is using. Match their communication style.",
    "not_helpful": "Focus on directly answering the user's question with practical, applicable advice.",
    "too_long": "Keep responses concise. Aim for 2-4 paragraphs unless the user asks for detail.",
    "too_short": "Provide enough detail to be useful. Include rationale and specific guidance.",
    "missing_safety": "Always include safety reminders for injury risks, proper form, and medical consultation.",
    "repetitive": "Vary your responses. Don't repeat the same advice in every message.",
}

# Minimum number of feedback entries before a pattern is considered reliable
MIN_FEEDBACK_FOR_PATTERN = 3
# How far back to look for feedback patterns
FEEDBACK_WINDOW_DAYS = 60
# Weight decay: older feedback has less influence
DECAY_HALF_LIFE_DAYS = 30


class FeedbackCollector:
    """Collects and indexes user feedback for pattern analysis."""

    def __init__(self, db: Session):
        self.db = db

    def get_recent_feedback(
        self,
        user_id: UUID,
        days: int = FEEDBACK_WINDOW_DAYS,
        min_rating: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent feedback entries for a user."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = (
            self.db.query(models.UserFeedback)
            .filter(
                models.UserFeedback.user_id == user_id,
                models.UserFeedback.created_at >= cutoff,
            )
        )
        if min_rating is not None:
            q = q.filter(models.UserFeedback.rating <= min_rating)
        results = q.order_by(models.UserFeedback.created_at.desc()).all()

        return [
            {
                "id": str(f.id),
                "rating": f.rating,
                "category": f.category,
                "comment": f.comment,
                "coach_reply_snapshot": f.coach_reply_snapshot or "",
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "age_days": (
                    (datetime.utcnow() - f.created_at).days
                    if f.created_at
                    else 999
                ),
            }
            for f in results
        ]

    def get_low_rated(
        self, user_id: UUID, days: int = FEEDBACK_WINDOW_DAYS
    ) -> list[dict[str, Any]]:
        """Get feedback rated 1-2 stars (negative signals)."""
        return self.get_recent_feedback(user_id, days=days, min_rating=3)

    def get_rating_trend(
        self, user_id: UUID, days: int = FEEDBACK_WINDOW_DAYS
    ) -> dict[str, Any]:
        """Compute rating trend over time."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        results = (
            self.db.query(
                func.date(models.UserFeedback.created_at).label("day"),
                func.avg(models.UserFeedback.rating).label("avg_rating"),
                func.count(models.UserFeedback.id).label("count"),
            )
            .filter(
                models.UserFeedback.user_id == user_id,
                models.UserFeedback.created_at >= cutoff,
            )
            .group_by(func.date(models.UserFeedback.created_at))
            .order_by(func.date(models.UserFeedback.created_at))
            .all()
        )
        return {
            "trend": [
                {"date": str(row.day), "avg_rating": round(float(row.avg_rating), 2), "count": row.count}
                for row in results
            ],
            "overall_avg": round(
                float(
                    self.db.query(func.avg(models.UserFeedback.rating))
                    .filter(
                        models.UserFeedback.user_id == user_id,
                        models.UserFeedback.created_at >= cutoff,
                    )
                    .scalar()
                    or 0
                ),
                2,
            ),
        }

    def get_top_categories(
        self, user_id: UUID, days: int = FEEDBACK_WINDOW_DAYS, max_rating: int = 2
    ) -> list[dict[str, Any]]:
        """Get most common complaint categories (low-rated feedback)."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        results = (
            self.db.query(
                models.UserFeedback.category,
                func.count(models.UserFeedback.id).label("count"),
            )
            .filter(
                models.UserFeedback.user_id == user_id,
                models.UserFeedback.created_at >= cutoff,
                models.UserFeedback.rating <= max_rating,
                models.UserFeedback.category.isnot(None),
            )
            .group_by(models.UserFeedback.category)
            .order_by(func.count(models.UserFeedback.id).desc())
            .limit(5)
            .all()
        )
        return [{"category": row.category, "count": row.count} for row in results]


class PreferenceLearner:
    """Aggregates feedback patterns into actionable preferences."""

    def __init__(self, collector: FeedbackCollector):
        self.collector = collector

    def learn(self, user_id: UUID) -> dict[str, Any]:
        """Learn user preferences from their feedback history.

        Returns a dict with:
        - dominant_complaints: what the user most often rates poorly
        - behavioral_guidance: natural-language instructions for the LLM
        - confidence: how reliable these patterns are (0.0-1.0)
        - evidence_summary: human-readable explanation of the patterns
        """
        low_rated = self.collector.get_low_rated(user_id)
        top_categories = self.collector.get_top_categories(user_id)
        trend = self.collector.get_rating_trend(user_id)

        behavioral_guidance: list[str] = []
        evidence: list[str] = []
        total_signals = len(low_rated)

        # If not enough feedback, return empty (insufficient data)
        if total_signals < MIN_FEEDBACK_FOR_PATTERN:
            return {
                "dominant_complaints": [],
                "behavioral_guidance": [],
                "confidence": 0.0,
                "evidence_summary": (
                    f"Only {total_signals} negative feedback entries "
                    f"(need {MIN_FEEDBACK_FOR_PATTERN} for reliable patterns)"
                ),
                "total_negative_feedback": total_signals,
                "trend": trend,
            }

        # Convert top complaint categories into behavioral guidance
        for cat in top_categories:
            guidance = CORRECTION_CATEGORIES.get(cat["category"])
            if guidance:
                behavioral_guidance.append(guidance)
                evidence.append(
                    f"User rated {cat['count']} replies as '{cat['category']}' "
                    f"(rating <= 2)"
                )

        # Apply time-based decay to confidence
        confidence = min(0.9, total_signals / (MIN_FEEDBACK_FOR_PATTERN * 3))
        if total_signals >= 10:
            confidence = 0.9

        # Check for trend degradation
        if trend["overall_avg"] < 3.0 and len(trend.get("trend", [])) >= 3:
            behavioral_guidance.append(
                "The user's overall satisfaction is declining. "
                "Put extra care into understanding their current needs. "
                "Ask clarifying questions before giving advice."
            )
            evidence.append(
                f"Recent overall average rating is {trend['overall_avg']}/5 — declining trend"
            )
            confidence = min(0.95, confidence + 0.1)

        return {
            "dominant_complaints": [c["category"] for c in top_categories],
            "behavioral_guidance": behavioral_guidance,
            "confidence": round(confidence, 2),
            "evidence_summary": "; ".join(evidence),
            "total_negative_feedback": total_signals,
            "trend": trend,
        }


class PromptEnhancer:
    """Injects learned preferences from feedback into system prompts."""

    def __init__(self, learner: PreferenceLearner):
        self.learner = learner

    def enhance_system_prompt(
        self, user_id: UUID, base_prompt: str
    ) -> tuple[str, dict[str, Any]]:
        """Enhance a system prompt with user-specific learned preferences.

        Returns (enhanced_prompt, debug_info).

        The enhanced prompt appends a "Learned Preferences" section that tells
        the LLM what this specific user likes and dislikes, backed by their
        actual feedback history.
        """
        preferences = self.learner.learn(user_id)

        if not preferences["behavioral_guidance"]:
            return base_prompt, {
                "enhanced": False,
                "reason": preferences["evidence_summary"] or "no patterns learned",
                "confidence": preferences["confidence"],
            }

        guidance_text = "\n".join(
            f"- {g}" for g in preferences["behavioral_guidance"]
        )

        learned_section = (
            f"\n\n## Learned Preferences (from user feedback)\n"
            f"Based on the user's past feedback ({preferences['total_negative_feedback']} negative signals), "
            f"please adapt your responses as follows:\n"
            f"{guidance_text}\n"
            f"\nConfidence in these patterns: {preferences['confidence']:.0%}. "
            f"Evidence: {preferences['evidence_summary']}"
        )

        enhanced = base_prompt + learned_section
        return enhanced, {
            "enhanced": True,
            "confidence": preferences["confidence"],
            "guidance_count": len(preferences["behavioral_guidance"]),
            "dominant_complaints": preferences["dominant_complaints"],
            "evidence": preferences["evidence_summary"],
        }


def build_adaptive_system_prompt(
    db: Session,
    user_id: UUID,
    prompt_id: str,
    prompt_factory,
) -> tuple[str, dict[str, Any]]:
    """One-stop function: get prompt, enhance with feedback, return both.

    Usage in coach_agent.py:
        system_prompt, feedback_debug = build_adaptive_system_prompt(
            self.db, user_id, "coach_coaching_reply", registry.get
        )
    """
    base_prompt = prompt_factory(prompt_id)
    collector = FeedbackCollector(db)
    learner = PreferenceLearner(collector)
    enhancer = PromptEnhancer(learner)
    return enhancer.enhance_system_prompt(user_id, base_prompt)
