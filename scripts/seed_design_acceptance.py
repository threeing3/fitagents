"""Create new synthetic UI fixtures only in the named isolated acceptance database."""

import json
import os
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from scripts.run_local_byok import LocalModelAccess, build_environment

DSN = "postgresql+psycopg://fitagent_test@127.0.0.1:15432/fitagent_acceptance_20261002"


def main():
    os.environ.update(build_environment(LocalModelAccess("offline"), DSN, 8015))
    from fast_api.app.db import models
    from fast_api.app.db.database import SessionLocal
    from fast_api.app.services.approved_plan_adjustments import ApprovedPlanAdjustmentService
    from fast_api.app.services.responsibilities import ResponsibilityService

    email = f"design-{uuid.uuid4().hex[:10]}@example.com"
    with httpx.Client(base_url="http://127.0.0.1:8015", timeout=20) as client:
        response = client.post(
            "/v1/auth/register",
            json={
                "email": email,
                "password": "Synthetic-only-design-1234",
                "display_name": "合成设计验收",
            },
        )
        response.raise_for_status()
        user_id = uuid.UUID(response.json()["user_id"])
        profile = client.post(
            "/v1/profiles",
            json={
                "age": 25,
                "sex": "male",
                "height_cm": 175,
                "weight_kg": 70,
                "goal": "maintenance",
                "experience_level": "intermediate",
                "equipment_available": ["dumbbell"],
            },
        )
        profile.raise_for_status()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    dates = [(today + timedelta(days=offset)).isoformat() for offset in (1, 3, 5)]
    with SessionLocal() as db:
        plan = models.TrainingPlan(
            user_id=user_id,
            status="active",
            week_start=today,
            plan_json={
                "training_days": [
                    {
                        "date": day,
                        "name": "合成全身力量（验收用）",
                        "exercises": [
                            {"name": name, "sets": 3, "reps": 10} for name in ("深蹲", "哑铃划船")
                        ],
                    }
                    for day in dates
                ]
            },
        )
        db.add(plan)
        db.flush()
        task = ResponsibilityService(db).create_weekly(
            user_id, "合成验收：每周复盘四周，任何修改必须先由我批准"
        )
        proposal = ApprovedPlanAdjustmentService(db).propose(
            user_id, task.id, plan.id, dates[0], 1, "合成设计验收草案，不是医学或模型结论"
        )
        db.commit()
    print(
        json.dumps(
            {
                "email": email,
                "user_id": str(user_id),
                "dates": dates,
                "approval_id": proposal["approval"]["approval_id"],
                "database": "fitagent_acceptance_20261002",
                "synthetic": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
