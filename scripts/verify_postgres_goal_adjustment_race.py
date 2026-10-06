"""Actual claimed adjustment versus a concurrent goal correction, synthetic only."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.approval_manager import ApprovalManager
from fast_api.app.services.approved_plan_adjustments import ApprovedPlanAdjustmentService
from fast_api.app.services.background_tasks import BackgroundTaskQueue, run_one_background_task
from fast_api.app.services.plan_adjustment_policy import PlanAdjustmentPolicy
from fast_api.app.services.plan_writes import lock_plan_owner
from fast_api.app.services.responsibilities import ResponsibilityService
from scripts.verify_postgres_followup_race import engine_for, receive


def worker(dsn, schema, user_id, channel):
    engine = engine_for(dsn, schema, "adjustment")
    original_claim = BackgroundTaskQueue.claim_next

    def gated_claim(queue, **kwargs):
        job = original_claim(queue, **kwargs)
        assert job is not None
        channel.send("claimed")
        assert receive(channel) == "execute"
        channel.send("attempting")
        return job

    BackgroundTaskQueue.claim_next = gated_claim
    try:
        with Session(engine) as db:
            result = run_one_background_task(db, user_id=user_id)
            channel.send({"status": result.status, "result": result.result_json})
    except Exception as exc:
        channel.send({"error": str(exc)})
    finally:
        BackgroundTaskQueue.claim_next = original_claim
        engine.dispose()
        channel.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    if "127.0.0.1:15432" not in args.dsn:
        raise ValueError("Only the dedicated loopback test port is allowed")
    schema = "fitagent_goal_race_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    engine = engine_for(args.dsn, schema, "observer")
    user_id = uuid.uuid4()
    process = parent = None
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            Base.metadata.create_all(connection)
        tomorrow = (
            datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()
            + timedelta(days=1)
        ).isoformat()
        baseline = {
            "training_days": [{"date": tomorrow, "exercises": [{"name": "row", "sets": 4}]}]
        }
        with Session(engine) as db:
            db.add(
                models.User(id=user_id, email="goal-race@example.test", password_hash="synthetic")
            )
            db.flush()
            db.add(models.UserProfile(user_id=user_id, goal="fat_loss", injuries=[]))
            task = ResponsibilityService(db).create_weekly(user_id, "复盘；改计划先问")
            plan = models.TrainingPlan(user_id=user_id, status="active", plan_json=baseline)
            db.add(plan)
            db.flush()
            proposal = ApprovedPlanAdjustmentService(db).propose(
                user_id, task.id, plan.id, tomorrow, 1, "synthetic fatigue review"
            )
            approval_id = proposal["approval"]["approval_id"]
            assert ApprovalManager(db).approve(approval_id) is not None
            db.commit()
            plan_id = plan.id
        print("Created approved synthetic adjustment in", schema, flush=True)
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=worker, args=(args.dsn, schema, user_id, child))
        process.start()
        child.close()
        assert receive(parent) == "claimed"
        with Session(engine) as correcting:
            lock_plan_owner(correcting, user_id)
            correcting.get(models.UserProfile, user_id).goal = "muscle_gain"
            invalidated = PlanAdjustmentPolicy(correcting).invalidate_changed(user_id)
            assert approval_id in invalidated
            parent.send("execute")
            assert receive(parent) == "attempting"
            deadline = time.monotonic() + 15
            waiting = False
            while time.monotonic() < deadline:
                with engine.connect() as connection:
                    waiting = bool(
                        connection.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE "
                                "application_name='fitagent_followup_race_adjustment' AND wait_event_type='Lock'"
                            )
                        )
                    )
                if waiting:
                    break
                time.sleep(0.1)
            assert waiting, "Claimed adjustment did not wait on correction owner lock"
            print("Observed claimed adjustment waiting for goal correction", flush=True)
            correcting.commit()
        result = receive(parent)
        assert "error" not in result, result
        assert result["status"] == "completed", result
        assert result["result"] == {"status": "skipped", "reason": "approval_not_active"}, result
        with Session(engine) as db:
            assert db.get(models.TrainingPlan, plan_id).plan_json == baseline
            assert db.get(models.UserProfile, user_id).goal == "muscle_gain"
            assert db.get(models.PendingApproval, uuid.UUID(approval_id)).status == "stale"
        print(
            "PASS: new goal preserved; old approval stale; claimed adjustment skipped; plan unchanged",
            flush=True,
        )
        print("Schema retained; no records or files deleted", flush=True)
    finally:
        if process is not None:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        if parent is not None:
            parent.close()
        engine.dispose()


if __name__ == "__main__":
    main()
