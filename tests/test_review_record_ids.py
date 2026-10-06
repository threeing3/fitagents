"""Actual SQL retrieval keeps source IDs and authenticated ownership boundaries."""

import uuid
from datetime import date, datetime

from fast_api.app.db import models
from fast_api.app.services.context_builder import FitnessRetrievalService
from tests.test_api_integration import _create_client_and_db


def test_review_record_queries_return_actual_ids_not_other_users_records():
    _, factory = _create_client_and_db()
    with factory() as db:
        owner, other = uuid.uuid4(), uuid.uuid4()
        db.add_all(
            [
                models.User(id=owner, email="review-owner@example.test", password_hash="disabled"),
                models.User(id=other, email="review-other@example.test", password_hash="disabled"),
            ]
        )
        db.flush()
        rows = []
        for user in (owner, other):
            rows.extend(
                [
                    models.WorkoutLog(user_id=user, performed_at=datetime.utcnow()),
                    models.NutritionDailySummary(user_id=user, summary_date=date.today()),
                    models.RecoveryLog(user_id=user, log_date=date.today()),
                    models.SymptomLog(
                        user_id=user, symptom_date=date.today(), symptom_type="synthetic"
                    ),
                ]
            )
        db.add_all(rows)
        db.commit()
        retrieval = FitnessRetrievalService(db)
        queries = [
            retrieval.get_recent_workout_logs,
            retrieval.get_recent_nutrition_summary,
            retrieval.get_recent_recovery_logs,
            retrieval.get_recent_symptom_logs,
        ]
        for index, query in enumerate(queries):
            result = query(owner)
            assert len(result) == 1
            assert result[0]["id"] == str(rows[index].id)
            assert result[0]["id"] != str(rows[index + 4].id)
