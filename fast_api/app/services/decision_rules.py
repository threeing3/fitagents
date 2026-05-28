from typing import Any


class TrainingDecisionRules:
    """MVP progression, hold, deload, and risk rules for strength training."""

    HIGH_RISK_TERMS = ["chest_pain", "dizzy", "numb", "sharp_pain", "breathing_difficulty", "胸闷", "头晕", "刺痛", "呼吸困难"]

    def evaluate_progression(
        self,
        exercise_name: str,
        exercise_history: list[dict[str, Any]],
        recovery_logs: list[dict[str, Any]] | None = None,
        risk_notes: list[dict[str, Any]] | None = None,
        symptom_logs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        recovery_logs = recovery_logs or []
        risk_notes = risk_notes or []
        symptom_logs = symptom_logs or []

        high_risk = self._has_high_risk(risk_notes, symptom_logs)
        if high_risk:
            return {
                "decision_type": "risk_warning",
                "decision_result": "avoid_high_intensity_training",
                "reason": "High-risk symptoms or active risk notes are present.",
                "confidence_score": 0.9,
            }

        recent_sets = [item for item in exercise_history if item.get("completed") is not False][:8]
        avg_rpe = self._average([item.get("rpe") for item in recent_sets])
        pain_present = any((item.get("pain_score") or 0) >= 3 for item in exercise_history[:8])
        fatigue_high = any((item.get("fatigue_score") or 0) >= 8 for item in recovery_logs[:3])
        sleep_low = any((item.get("sleep_hours") or 24) < 6 for item in recovery_logs[:3])

        if pain_present or avg_rpe >= 9.5:
            return {
                "decision_type": "deload",
                "decision_result": "reduce_load_5_to_10_percent_or_substitute",
                "reason": "Recent exercise history shows pain or very high RPE.",
                "confidence_score": 0.82,
            }

        if avg_rpe > 8.5 or fatigue_high or sleep_low:
            return {
                "decision_type": "progression_adjustment",
                "decision_result": "hold_load_and_reduce_accessory_volume",
                "reason": "Completion is acceptable but recovery or RPE suggests holding load.",
                "confidence_score": 0.78,
            }

        if len(recent_sets) >= 2 and avg_rpe <= 8.5:
            increment = "5kg" if self._is_lower_body(exercise_name) else "2.5kg"
            return {
                "decision_type": "progression_adjustment",
                "decision_result": f"increase_next_session_by_{increment}",
                "reason": "Recent completed sets are within target effort and no risk signal is present.",
                "confidence_score": 0.76,
            }

        return {
            "decision_type": "progression_adjustment",
            "decision_result": "keep_current_load_until_more_data",
            "reason": "Not enough clean recent performance data for progression.",
            "confidence_score": 0.62,
        }

    def _has_high_risk(
        self,
        risk_notes: list[dict[str, Any]],
        symptom_logs: list[dict[str, Any]],
    ) -> bool:
        for note in risk_notes:
            if note.get("status") in {"active", "monitoring"} and (note.get("severity_score") or 0) >= 0.8:
                return True
            text = f"{note.get('risk_type', '')} {note.get('description', '')}".lower()
            if any(term in text for term in self.HIGH_RISK_TERMS):
                return True
        for symptom in symptom_logs:
            if (symptom.get("severity_score") or 0) >= 8:
                return True
            text = f"{symptom.get('symptom_type', '')} {symptom.get('trigger_context', '')}".lower()
            if any(term in text for term in self.HIGH_RISK_TERMS):
                return True
        return False

    def _average(self, values: list[Any]) -> float:
        numeric = [float(value) for value in values if value is not None]
        if not numeric:
            return 10.0
        return sum(numeric) / len(numeric)

    def _is_lower_body(self, exercise_name: str) -> bool:
        lowered = exercise_name.lower()
        return any(term in lowered for term in ["squat", "deadlift", "leg", "深蹲", "硬拉", "腿"])
