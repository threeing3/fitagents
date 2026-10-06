"""Read-only snapshot adapter for the existing deterministic weekly reflection.

Reuse production builders, not invented model predictions. The input must already
be frozen to the same date window for all arms. No session, retain_memory or writes.
"""

from datetime import date, datetime

from fast_api.app.db import models
from fast_api.app.services.reflection_service import ReflectionService


def _rows(packet, field, model, date_field, fields, start, end):
    result = []
    for raw in packet.get(field, []):
        if not raw.get("id"):
            raise ValueError("Frozen business records require source IDs")
        source_date = raw.get("performed_at") if date_field == "performed_at" else raw.get("date")
        if not isinstance(source_date, str):
            raise ValueError("Frozen business records require dates")
        when = (
            datetime.fromisoformat(source_date)
            if date_field == "performed_at"
            else date.fromisoformat(source_date)
        )
        day = when.date() if isinstance(when, datetime) else when
        if not start <= day <= end:
            raise ValueError("All arms must receive records within the frozen review window")
        values = {name: raw.get(name) for name in fields}
        values.update(id=raw["id"], **{date_field: when})
        result.append(model(**values))
    return result


async def existing_review_workflow(packet, message, *, max_calls, timeout_seconds):
    del message
    if max_calls < 0 or timeout_seconds <= 0:
        raise ValueError("Invalid evaluation budget")
    window = packet.get("review_window") or {}
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    if start > end:
        raise ValueError("Invalid review window")
    training = _rows(
        packet,
        "recent_training",
        models.WorkoutLog,
        "performed_at",
        ("rpe", "duration_minutes", "completion_rate", "workout_name", "notes"),
        start,
        end,
    )
    nutrition = _rows(
        packet,
        "recent_nutrition",
        models.NutritionDailySummary,
        "summary_date",
        ("adherence_score", "total_protein_g", "summary_text"),
        start,
        end,
    )
    recovery = _rows(
        packet,
        "recent_recovery",
        models.RecoveryLog,
        "log_date",
        ("sleep_hours", "fatigue_score", "notes"),
        start,
        end,
    )
    symptoms = _rows(
        packet,
        "recent_symptoms",
        models.SymptomLog,
        "symptom_date",
        ("symptom_type", "severity_score", "status"),
        start,
        end,
    )
    # Builders operate only on provided rows. Avoid constructing a database-backed manager.
    service = object.__new__(ReflectionService)
    specs = [
        service._build_weekly_training_observation(training, [], start, end),
        service._build_weekly_nutrition_observation(nutrition, start, end),
        service._build_weekly_recovery_observation(recovery, symptoms, start, end),
        service._build_weekly_opinion(training, nutrition, recovery, symptoms, [], start, end),
    ]
    specs = [spec for spec in specs if spec]
    # Shared child tools expose workouts, not exercise-set rows. Do not report
    # the adapter's empty set input as a measured absence of exercises.
    for spec in specs:
        if spec.get("fact_kind") == "weekly_training_observation":
            spec["content"] = spec["content"].replace("exercise_sets=0", "exercise_sets=unknown")
    signal = service._weekly_adjustment_signal(recovery, symptoms, None, None)
    return {
        "results": [
            {
                "status": "completed",
                "domain": "existing_weekly_reflection",
                "summary": "\n".join(spec["content"] for spec in specs)
                or "No dated records available for this review.",
                "recommendations": [],
                "uncertainties": [
                    "Missing records are not evidence of non-adherence.",
                    "Exercise-set detail is unavailable in the shared snapshot.",
                ],
                "evidence_ids": sorted({item["id"] for spec in specs for item in spec["evidence"]}),
                "adjustment_signal": signal,
                "model_called": False,
                "model_calls": 0,
            }
        ],
        "model_calls": 0,
        "model_called": False,
        "implementation": "existing_reflection_builders_read_only_snapshot",
        "no_business_writes": True,
    }
