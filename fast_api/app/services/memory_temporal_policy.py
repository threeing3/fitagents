from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import pow
from typing import Any


@dataclass(frozen=True)
class TemporalProfile:
    name: str
    half_life_days: float | None
    floor: float


@dataclass(frozen=True)
class TemporalScore:
    reference_time: datetime | None
    age_days: float | None
    half_life_days: float | None
    floor: float
    score: float
    policy: str


@dataclass(frozen=True)
class ApplicabilityScore:
    score: float
    adjustment: float
    reasons: list[str]


class MemoryTemporalPolicy:
    """Compute dynamic recency and current-context applicability for memories."""

    PROFILES = {
        "health_fact": TemporalProfile("health_fact_no_decay", None, 1.0),
        "current_recovery": TemporalProfile("current_recovery", 5.0, 0.0),
        "symptom": TemporalProfile("symptom_event", 7.0, 0.10),
        "training": TemporalProfile("training_performance", 21.0, 0.10),
        "nutrition": TemporalProfile("nutrition_behavior", 28.0, 0.10),
        "weekly_observation": TemporalProfile("weekly_observation", 42.0, 0.15),
        "opinion": TemporalProfile("coach_opinion", 21.0, 0.05),
        "preference": TemporalProfile("user_preference", 180.0, 0.30),
        "successful_strategy": TemporalProfile("successful_strategy", 120.0, 0.25),
        "failed_strategy": TemporalProfile("failed_strategy", 180.0, 0.35),
        "safety_failed_strategy": TemporalProfile("safety_failed_strategy", 365.0, 0.60),
        "unknown": TemporalProfile("unknown", 60.0, 0.15),
    }

    BASELINE_SCALES = {
        "fatigue": 10.0,
        "fatigue_score": 10.0,
        "soreness": 10.0,
        "soreness_score": 10.0,
        "sleep_hours": 4.0,
        "symptom_severity": 10.0,
        "max_symptom_severity": 10.0,
        "training_load": 1.0,
        "volume_multiplier": 1.0,
    }

    def score(self, memory: Any, as_of: datetime | None = None) -> TemporalScore:
        as_of = self._normalise_datetime(as_of or datetime.utcnow())
        profile = self.profile_for(memory)
        reference_time = self.reference_time(memory)
        if reference_time is None:
            return TemporalScore(
                reference_time=None,
                age_days=None,
                half_life_days=profile.half_life_days,
                floor=profile.floor,
                score=1.0 if profile.half_life_days is None else 0.5,
                policy=f"{profile.name}:missing_time",
            )

        reference_time = self._normalise_datetime(reference_time)
        age_days = max(0.0, (as_of - reference_time).total_seconds() / 86400.0)
        if profile.half_life_days is None:
            score = 1.0
        else:
            score = profile.floor + (1.0 - profile.floor) * pow(
                2.0,
                -age_days / profile.half_life_days,
            )
        return TemporalScore(
            reference_time=reference_time,
            age_days=round(age_days, 6),
            half_life_days=profile.half_life_days,
            floor=profile.floor,
            score=round(max(0.0, min(1.0, score)), 6),
            policy=profile.name,
        )

    def applicability(
        self,
        memory: Any,
        current_context: dict[str, Any] | None,
    ) -> ApplicabilityScore:
        fact_kind = str(getattr(memory, "fact_kind", "") or "")
        if fact_kind not in {"strategy_experience", "failed_strategy"}:
            return ApplicabilityScore(score=0.5, adjustment=0.0, reasons=["not_strategy_memory"])

        metadata = dict(getattr(memory, "memory_metadata", None) or {})
        context = current_context or {}
        signals: list[float] = []
        reasons: list[str] = []

        memory_goal = self._normalise_text(metadata.get("goal"))
        current_goal = self._normalise_text(context.get("goal"))
        if memory_goal and current_goal:
            matched = memory_goal == current_goal
            signals.append(1.0 if matched else 0.0)
            reasons.append("goal_match" if matched else "goal_mismatch")

        memory_phase = self._normalise_text(metadata.get("training_phase"))
        current_phase = self._normalise_text(context.get("training_phase"))
        if memory_phase and current_phase:
            matched = memory_phase == current_phase
            signals.append(1.0 if matched else 0.0)
            reasons.append("training_phase_match" if matched else "training_phase_mismatch")

        memory_baseline = metadata.get("baseline_state") or {}
        current_baseline = context.get("baseline_state") or {}
        for key, scale in self.BASELINE_SCALES.items():
            left = self._number(memory_baseline.get(key))
            right = self._number(current_baseline.get(key))
            if left is None or right is None:
                continue
            similarity = max(0.0, 1.0 - abs(left - right) / scale)
            signals.append(similarity)
            reasons.append(f"baseline_{key}_similarity={similarity:.2f}")

        memory_entities = {
            str(item.get("canonical") or item.get("name") or "").lower()
            for item in (getattr(memory, "entities", None) or [])
            if isinstance(item, dict)
        }
        current_entities = {
            str(item.get("canonical") or item.get("name") or item).lower()
            for item in (context.get("entities") or [])
            if item
        }
        overlap = sorted((memory_entities & current_entities) - {""})
        if overlap:
            signals.append(1.0)
            reasons.append("entity_overlap=" + ",".join(overlap[:5]))

        if not signals:
            return ApplicabilityScore(
                score=0.5,
                adjustment=0.0,
                reasons=["legacy_or_insufficient_applicability_metadata"],
            )

        score = max(0.0, min(1.0, sum(signals) / len(signals)))
        adjustment = max(-0.10, min(0.10, (score - 0.5) * 0.20))
        return ApplicabilityScore(
            score=round(score, 6),
            adjustment=round(adjustment, 6),
            reasons=reasons,
        )

    def profile_for(self, memory: Any) -> TemporalProfile:
        fact_kind = str(getattr(memory, "fact_kind", "") or "").lower()
        memory_type = str(getattr(memory, "memory_type", "") or "").lower()
        network = str(getattr(memory, "memory_network", "") or "").lower()
        category = str(getattr(memory, "category", "") or "").lower()

        if fact_kind == "failed_strategy":
            key = "safety_failed_strategy" if self.is_safety_failed_strategy(memory) else "failed_strategy"
            return self.PROFILES[key]
        if fact_kind == "strategy_experience":
            return self.PROFILES["successful_strategy"]
        if fact_kind in {"health_fact", "medical_context"} or memory_type in {
            "health_fact",
            "medical_context",
            "risk_signal",
        }:
            return self.PROFILES["health_fact"]
        if fact_kind in {"recent_state", "recovery_event"} or memory_type == "recent_state":
            return self.PROFILES["current_recovery"]
        if fact_kind == "symptom_event" or category == "risk":
            return self.PROFILES["symptom"]
        if fact_kind in {
            "weekly_training_observation",
            "weekly_nutrition_observation",
            "weekly_recovery_observation",
            "weekly_summary",
        }:
            return self.PROFILES["weekly_observation"]
        if network == "opinion" or fact_kind == "coach_opinion":
            return self.PROFILES["opinion"]
        if fact_kind in {"preference", "plan_preference"} or category == "preference":
            return self.PROFILES["preference"]
        if fact_kind in {"training_performance", "workout_event"} or category == "training":
            return self.PROFILES["training"]
        if fact_kind in {"nutrition_habit", "nutrition_event"} or category == "nutrition":
            return self.PROFILES["nutrition"]
        return self.PROFILES["unknown"]

    def is_safety_failed_strategy(self, memory: Any) -> bool:
        metadata = dict(getattr(memory, "memory_metadata", None) or {})
        safety_status = self._normalise_text(metadata.get("safety_status"))
        if safety_status in {"worse", "worsened", "severe", "safety_escalation"}:
            return True
        if bool(metadata.get("safety_relevant")):
            return True
        category = self._normalise_text(getattr(memory, "category", None))
        entities = getattr(memory, "entities", None) or []
        has_safety_entity = any(
            isinstance(item, dict) and item.get("type") in {"symptom", "condition", "medication"}
            for item in entities
        )
        return category == "risk" or has_safety_entity

    def reference_time(self, memory: Any) -> datetime | None:
        for field in ("occurred_end", "occurred_start", "mentioned_at", "created_at"):
            value = getattr(memory, field, None)
            if isinstance(value, datetime):
                return value
        return None

    @staticmethod
    def _normalise_datetime(value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    @staticmethod
    def _normalise_text(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
