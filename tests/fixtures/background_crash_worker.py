"""Real approval handler, isolated file database, parent-controlled termination."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from fast_api.app.db import models  # noqa: E402
from fast_api.app.db.database import Base  # noqa: E402
from fast_api.app.services import (  # noqa: E402
    background_tasks as tasks,
)
from fast_api.app.services import (
    durable_stream_journal as journals,
)
from fast_api.app.services.approval_manager import ApprovalManager  # noqa: E402
from fast_api.app.services.approved_plan_adjustments import (
    ApprovedPlanAdjustmentService,  # noqa: E402
)
from fast_api.app.services.responsibilities import ResponsibilityService  # noqa: E402


def pause():
    print("READY", flush=True)
    sys.stdin.read()
    raise RuntimeError("Parent must terminate this isolated process")


def main():
    directory, phase = Path(sys.argv[1]), sys.argv[2]
    journals.get_settings = lambda: SimpleNamespace(agent_log_dir=str(directory))
    database_url = (
        sys.argv[3] if len(sys.argv) > 3 else f"sqlite:///{(directory / 'crash.sqlite').as_posix()}"
    )
    if database_url.startswith("postgresql"):
        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        url = make_url(database_url)
        if (
            url.host != "127.0.0.1"
            or url.port != 15433
            or not url.database.startswith("fitagent_crash_")
        ):
            raise ValueError("Dedicated crash database only")
        with psycopg.connect(
            host=url.host,
            port=url.port,
            user=url.username,
            password=url.password,
            dbname="postgres",
            autocommit=True,
        ) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(email="crash@example.test", password_hash="synthetic", timezone="UTC")
        db.add(user)
        db.add(models.User(email="other-crash@example.test", password_hash="synthetic"))
        db.flush()
        responsibility = ResponsibilityService(db).create_weekly(user.id, "synthetic")
        day = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
        plan = models.TrainingPlan(
            user_id=user.id,
            status="active",
            plan_json={"training_days": [{"date": day, "exercises": [{"name": "row", "sets": 4}]}]},
        )
        db.add(plan)
        db.commit()
        proposal = ApprovedPlanAdjustmentService(db).propose(
            user.id, responsibility.id, plan.id, day, 1, "synthetic fatigue review"
        )
        db.commit()
        ApprovalManager(db).approve(proposal["approval"]["approval_id"])
        db.commit()
        original_handler = tasks._execute_task
        original_success = tasks.BackgroundTaskQueue.mark_success
        original_append = journals.DurableStreamJournal.append

        def handler(db, task):
            if phase == "before_handler":
                pause()
            return original_handler(db, task)

        def success(queue, task, result, elapsed):
            if phase == "before_commit":
                # Handler has performed real transactional writes but no queue commit.
                pause()
            original_success(queue, task, result, elapsed)
            if phase == "after_commit":
                pause()

        def append(journal, item):
            original_append(journal, item)
            if (
                phase == "after_terminal"
                and item.get("type") == "journal.end"
                and item.get("state") == "completed"
            ):
                pause()

        tasks._execute_task = handler
        tasks.BackgroundTaskQueue.mark_success = success
        journals.DurableStreamJournal.append = append
        tasks.run_one_background_task(db, user_id=user.id)


if __name__ == "__main__":
    main()
