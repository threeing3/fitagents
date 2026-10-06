"""Run the actual weekly-review queue with four isolated database workers."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.background_tasks import run_one_background_task
from fast_api.app.services.responsibilities import ResponsibilityService


def engine_for(dsn, schema):
    return create_engine(dsn, execution_options={"schema_translate_map": {None: schema}})


def worker(dsn, schema, barrier, results):
    engine = engine_for(dsn, schema)
    processed = []
    try:
        barrier.wait(timeout=30)
        with Session(engine) as db:
            scanned = ResponsibilityService(db).scan_due()
            db.commit()
        barrier.wait(timeout=30)
        with Session(engine) as db:
            while True:
                task = run_one_background_task(db)
                if task is None:
                    break
                processed.append(str(task.id))
                print("Worker completed", task.id, task.status, flush=True)
        results.put({"processed": processed, "scan": scanned})
    except Exception as exc:
        results.put({"error": str(exc), "processed": processed})
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    if "127.0.0.1:15432" not in args.dsn:
        raise ValueError("Only the dedicated loopback test port is allowed")
    schema = "fitagent_multiworker_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    engine = engine_for(args.dsn, schema)
    processes = []
    context = mp.get_context("spawn")
    results = context.Queue()
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            Base.metadata.create_all(connection)
        with Session(engine) as db:
            for index in range(4):
                user = models.User(
                    id=uuid.uuid4(),
                    email=f"worker-{index}@example.test",
                    password_hash="synthetic",
                    timezone="Asia/Shanghai",
                )
                db.add(user)
                db.flush()
                for _ in range(3):
                    ResponsibilityService(db).create_weekly(
                        user.id, "合成每周复盘", now=datetime.now(timezone.utc) - timedelta(days=8)
                    )
            db.commit()
        print("Created 4 synthetic users and 12 responsibilities in", schema, flush=True)
        barrier = context.Barrier(4)
        for _ in range(4):
            process = context.Process(target=worker, args=(args.dsn, schema, barrier, results))
            process.start()
            processes.append(process)
        reports = [results.get(timeout=60) for _ in processes]
        for report in reports:
            assert "error" not in report, report
        claimed_ids = [item for report in reports for item in report["processed"]]
        assert len(claimed_ids) == len(set(claimed_ids)) == 12, reports
        with Session(engine) as db:
            jobs = db.scalars(select(models.BackgroundTask)).all()
            tasks = db.scalars(select(models.AgentTaskState)).all()
            events = db.scalars(
                select(models.AgentTaskEvent).where(
                    models.AgentTaskEvent.event_type == "responsibility.reviewed"
                )
            ).all()
            assert len(jobs) == len(tasks) == len(events) == 12
            for job in jobs:
                assert job.status == "completed" and job.attempts == 1
                assert job.result_json["status"] == "reviewed"
                task = db.get(
                    models.AgentTaskState, uuid.UUID(job.result_json["responsibility_id"])
                )
                assert task.user_id == job.user_id
                assert task.progress_json["reviews_completed"] == 1
            assert {str(job.id) for job in jobs} == set(claimed_ids)
        print("PASS: 12 unique claims; 12 completed reviews; attempts=1; owners match", flush=True)
        print("Per-worker counts:", [len(report["processed"]) for report in reports], flush=True)
        print("Schema retained; no files or records deleted", flush=True)
    finally:
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        engine.dispose()


if __name__ == "__main__":
    main()
