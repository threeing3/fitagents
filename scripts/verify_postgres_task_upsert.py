"""Two-process synthetic task-upsert check on the dedicated loopback database."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.agent_task_state import AgentTaskStateService


def engine_for(dsn: str, schema: str, role: str):
    return create_engine(
        dsn,
        connect_args={"application_name": "fitagent_task_upsert_" + role},
        execution_options={"schema_translate_map": {None: schema}},
    )


def upsert(db: Session, user_id: uuid.UUID, revision: int) -> uuid.UUID:
    task = AgentTaskStateService(db)._upsert_task(
        user_id=user_id,
        task_type="fitness_cycle",
        title="Synthetic training cycle",
        objective="Validate one active row across processes",
        phase="observe",
        current_step=f"revision {revision}",
        success_metrics={},
        constraints={},
        next_actions=[],
        progress_patch={"revision": revision},
        agent_run_id=None,
    )
    return task.id


def writer(dsn: str, schema: str, user_id: uuid.UUID, role: str, channel) -> None:
    engine = engine_for(dsn, schema, role)
    try:
        with Session(engine) as db:
            if role == "first":
                task_id = upsert(db, user_id, 1)
                channel.send("created")
                if not channel.poll(30) or channel.recv() != "release":
                    raise TimeoutError("Parent did not release first writer")
            else:
                channel.send("attempting")
                task_id = upsert(db, user_id, 2)
            db.commit()
            channel.send(str(task_id))
    except Exception as exc:
        channel.send("error:" + str(exc))
    finally:
        engine.dispose()
        channel.close()


def receive(channel):
    if not channel.poll(30):
        raise TimeoutError("Worker response timed out")
    return channel.recv()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    if "127.0.0.1:15432" not in args.dsn:
        raise ValueError("Only the dedicated loopback test port is allowed")
    schema = "fitagent_task_upsert_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    engine = engine_for(args.dsn, schema, "observer")
    user_id = uuid.uuid4()
    workers = []
    channels = []
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for table in (
                models.User.__table__,
                models.ConversationSession.__table__,
                models.AgentRun.__table__,
                models.AgentTaskState.__table__,
                models.AgentTaskEvent.__table__,
            ):
                table.create(connection)
        with Session(engine) as db:
            db.add(
                models.User(
                    id=user_id,
                    email="task-upsert-isolated@example.test",
                    password_hash="synthetic",
                )
            )
            db.commit()
        print("Created synthetic-only schema:", schema, flush=True)
        context = mp.get_context("spawn")
        for role in ("first", "second"):
            parent, child = context.Pipe()
            process = context.Process(target=writer, args=(args.dsn, schema, user_id, role, child))
            process.start()
            child.close()
            workers.append(process)
            channels.append(parent)
            assert receive(parent) == ("created" if role == "first" else "attempting")
        deadline = time.monotonic() + 15
        waiting = False
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                waiting = bool(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE "
                            "application_name='fitagent_task_upsert_second' "
                            "AND wait_event_type='Lock'"
                        )
                    )
                )
            if waiting:
                break
            time.sleep(0.1)
        assert waiting, "Second process did not wait on a real PostgreSQL lock"
        print("Observed second writer waiting on owner lock", flush=True)
        channels[0].send("release")
        first_id = receive(channels[0])
        second_id = receive(channels[1])
        assert not first_id.startswith("error:"), first_id
        assert not second_id.startswith("error:"), second_id
        assert first_id == second_id
        with Session(engine) as db:
            tasks = db.scalars(select(models.AgentTaskState)).all()
            events = db.scalars(select(models.AgentTaskEvent)).all()
            assert len(tasks) == 1
            assert tasks[0].progress_json["revision"] == 2
            assert [event.event_type for event in events] == ["created", "updated"]
        print("PASS: one task, two events, final revision 2", flush=True)
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
