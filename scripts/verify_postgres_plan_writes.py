"""Isolated PostgreSQL two-process plan-write validation, no deletion or real users."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.plan_writes import (
    StalePlanWriteError,
    lock_plan_owner,
    replace_plan_content,
)

BASELINE = {"training_days": [{"date": "2026-10-10", "sets": 4}]}
WINNER = {"training_days": [{"date": "2026-10-10", "sets": 3}]}


def engine_for(dsn, schema, application_name):
    return create_engine(
        dsn,
        connect_args={"application_name": application_name},
        execution_options={"schema_translate_map": {None: schema}},
    )


def writer(dsn, schema, owner_id, plan_id, role, channel):
    engine = engine_for(dsn, schema, "fitagent_isolated_" + role)
    try:
        with Session(engine) as db:
            if role == "first":
                lock_plan_owner(db, owner_id)
                channel.send("owner_locked")
                if not channel.poll(30) or channel.recv() != "release":
                    raise TimeoutError("Parent did not release first writer")
                replace_plan_content(db, owner_id, plan_id, BASELINE, WINNER)
                db.commit()
                channel.send("committed")
            else:
                channel.send("attempting")
                try:
                    replace_plan_content(db, owner_id, plan_id, BASELINE, {"stale": True})
                except StalePlanWriteError:
                    db.rollback()
                    channel.send("stale_rejected")
                else:
                    db.commit()
                    channel.send("unexpected_commit")
    except Exception as exc:
        channel.send("error:" + str(exc))
    finally:
        engine.dispose()
        channel.close()


def receive(channel):
    if not channel.poll(30):
        raise TimeoutError("Worker response timed out")
    return channel.recv()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    if "127.0.0.1:15432" not in args.dsn:
        raise ValueError("Only the dedicated loopback test port is allowed")
    schema = "fitagent_isolated_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    engine = engine_for(args.dsn, schema, "fitagent_isolated_observer")
    owner_id, plan_id = uuid.uuid4(), uuid.uuid4()
    workers = []
    channels = []
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            models.User.__table__.create(connection)
            models.TrainingPlan.__table__.create(connection)
        with Session(engine) as db:
            db.add(
                models.User(id=owner_id, email="isolated@example.test", password_hash="synthetic")
            )
            db.flush()
            db.add(
                models.TrainingPlan(
                    id=plan_id, user_id=owner_id, status="active", plan_json=BASELINE
                )
            )
            db.commit()
        print("Created synthetic-only schema:", schema, flush=True)
        context = mp.get_context("spawn")
        for role in ("first", "second"):
            parent, child = context.Pipe()
            process = context.Process(
                target=writer, args=(args.dsn, schema, owner_id, plan_id, role, child)
            )
            process.start()
            child.close()
            workers.append(process)
            channels.append(parent)
            assert receive(parent) == ("owner_locked" if role == "first" else "attempting")
        deadline = time.monotonic() + 15
        waiting = False
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                waiting = bool(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE application_name='fitagent_isolated_second' AND wait_event_type='Lock'"
                        )
                    )
                )
            if waiting:
                break
            time.sleep(0.1)
        assert waiting, "Second process did not show a real PostgreSQL lock wait"
        print("Observed second process waiting on database lock", flush=True)
        channels[0].send("release")
        assert receive(channels[0]) == "committed"
        assert receive(channels[1]) == "stale_rejected"
        with Session(engine) as db:
            assert db.get(models.TrainingPlan, plan_id).plan_json == WINNER
            replace_plan_content(db, owner_id, plan_id, WINNER, {"rollback": True})
            db.rollback()
        with Session(engine) as db:
            assert db.get(models.TrainingPlan, plan_id).plan_json == WINNER
        print(
            "PASS: real lock wait, stale-write rejection, winning state and outer rollback",
            flush=True,
        )
        print("Schema retained; no files or records deleted", flush=True)
    finally:
        for process in workers:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        for channel in channels:
            channel.close()
        engine.dispose()


if __name__ == "__main__":
    main()
