"""Kill only spawned synthetic workers; prove rollback vs uncertain commit on PG."""

import json
import multiprocessing as mp
import os
import uuid
from datetime import date, timedelta
from pathlib import Path


def configure():
    os.environ.update(
        DATABASE_URL="postgresql+psycopg://fitagent_test@127.0.0.1:15432/fitagent_acceptance_20261002",
        USE_PGVECTOR="false",
        LLM_PROVIDER="offline",
        EMBEDDING_PROVIDER="offline",
        CODE_DRIVEN_PLANNER="rule",
    )


def worker(owner_id, phase, channel):
    configure()
    from fast_api.app.db.database import SessionLocal
    from fast_api.app.services import background_tasks

    original = background_tasks._execute_task

    def pause_at_boundary(db, task):
        original(db, task)
        # Approved adjustments have no independent commit; plan.generate does.
        channel.send({"boundary": phase, "job_id": str(task.id)})
        if not channel.poll(60):
            raise TimeoutError("Fault injection parent did not finish")
        raise RuntimeError("This fault-injection worker must never resume execution")

    background_tasks._execute_task = pause_at_boundary
    try:
        with SessionLocal() as db:
            background_tasks.run_one_background_task(db, user_id=uuid.UUID(owner_id))
    except Exception as exc:
        channel.send({"error_type": type(exc).__name__})
    finally:
        channel.close()


def create_case(phase):
    from fast_api.app.db import models
    from fast_api.app.db.database import SessionLocal
    from fast_api.app.services.approval_manager import ApprovalManager
    from fast_api.app.services.approved_plan_adjustments import ApprovedPlanAdjustmentService
    from fast_api.app.services.background_tasks import BackgroundTaskQueue
    from fast_api.app.services.responsibilities import ResponsibilityService

    with SessionLocal() as db:
        user = models.User(
            email=f"recovery-{uuid.uuid4().hex[:12]}@example.com",
            password_hash="synthetic-not-login",
            timezone="Asia/Shanghai",
        )
        db.add(user)
        db.flush()
        db.add(
            models.UserProfile(
                user_id=user.id,
                age=25,
                sex="male",
                height_cm=175,
                weight_kg=70,
                goal="maintenance",
                experience_level="beginner",
                workout_frequency=3,
                workout_duration=60,
                equipment_available=["dumbbells"],
            )
        )
        day = (date.today() + timedelta(days=1)).isoformat()
        baseline = {
            "training_days": [{"date": day, "exercises": [{"name": "Row", "sets": 4, "reps": 10}]}],
            "nutrition": {"protein": 120},
        }
        plan = models.TrainingPlan(user_id=user.id, plan_json=baseline)
        db.add(plan)
        db.commit()
        if phase == "uncommitted_adjustment":
            responsibility = ResponsibilityService(db).create_weekly(
                user.id, "复盘自动，调整必须先批准"
            )
            proposal = ApprovedPlanAdjustmentService(db).propose(
                user.id, responsibility.id, plan.id, day, 1, "synthetic process interruption"
            )
            ApprovalManager(db).approve(proposal["approval"]["approval_id"])
            db.commit()
            job_id = uuid.UUID(proposal["job_id"])
        else:
            job_id = BackgroundTaskQueue(db).enqueue(user.id, "plan.generate", {"force": True}).id
        return user.id, job_id, plan.id, baseline


def main():
    configure()
    from fast_api.app.db import models
    from fast_api.app.db.database import SessionLocal, engine
    from fast_api.app.services.background_tasks import run_one_background_task
    from fast_api.app.services.task_recovery import TaskRecoveryBusyError, TaskRecoveryService

    assert engine.url.database == "fitagent_acceptance_20261002"
    results = []
    for phase in ("uncommitted_adjustment", "committed_generation"):
        owner, job_id, plan_id, baseline = create_case(phase)
        parent, child = mp.Pipe()
        process = mp.Process(target=worker, args=(str(owner), phase, child))
        process.start()
        child.close()
        try:
            if not parent.poll(60):
                raise TimeoutError("No actual worker boundary observed")
            boundary = parent.recv()
            assert boundary.get("boundary") == phase, boundary
            assert boundary["job_id"] == str(job_id)
            if phase == "uncommitted_adjustment":
                with SessionLocal() as db:
                    try:
                        TaskRecoveryService(db).reconcile(job_id, owner, 1)
                    except TaskRecoveryBusyError:
                        db.rollback()
                    else:
                        raise AssertionError("Live worker was not protected by execution lock")
                print("PASS: active worker write transaction refuses reconciliation", flush=True)
            # Deliberate fault injection only against the process created above.
            process.terminate()
            process.join(15)
            assert not process.is_alive()
            with SessionLocal() as db:
                job = db.get(models.BackgroundTask, job_id)
                assert job.status == "running"
                if phase == "uncommitted_adjustment":
                    assert db.get(models.TrainingPlan, plan_id).plan_json == baseline
                else:
                    assert (
                        db.query(models.TrainingPlan)
                        .filter_by(user_id=owner, status="active")
                        .one()
                        .id
                        != plan_id
                    )
                outcome = TaskRecoveryService(db).reconcile(job_id, owner, 1)
                db.commit()
                assert outcome["status"] == (
                    "failed" if phase == "uncommitted_adjustment" else "outcome_unknown"
                )
                assert outcome["requeued"] is False
                assert run_one_background_task(db, user_id=owner) is None
                results.append(
                    {
                        "phase": phase,
                        "outcome": outcome,
                        "synthetic_user_id": str(owner),
                        "job_id": str(job_id),
                    }
                )
            print("PASS:", phase, "reconciled without replay", flush=True)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(15)
            parent.close()
    Path("logs/refactor_process_recovery_20261002.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "scope": "actual spawned processes, migrated synthetic PostgreSQL, offline real business handlers",
                "cases": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    mp.freeze_support()
    main()
