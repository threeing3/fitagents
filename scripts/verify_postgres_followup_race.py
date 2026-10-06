"""Synthetic cancellation-versus-answer race on the dedicated loopback database."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.decision_evaluation import DecisionEvaluationService
from fast_api.app.services.plan_writes import lock_plan_owner


def engine_for(dsn, schema, role):
    return create_engine(
        dsn,
        connect_args={"application_name": "fitagent_followup_race_" + role},
        execution_options={"schema_translate_map": {None: schema}},
    )


def receive(channel):
    if not channel.poll(30):
        raise TimeoutError("Worker response timed out")
    return channel.recv()


def answer_worker(dsn, schema, user_id, followup_id, channel):
    engine = engine_for(dsn, schema, "answer")
    try:
        with Session(engine) as db:
            stale = db.get(models.DecisionFollowup, followup_id)
            assert stale.status == "pending"
            channel.send("cached_pending")
            assert receive(channel) == "answer"
            channel.send("attempting")
            try:
                DecisionEvaluationService(db).answer_followup(
                    followup_id, user_id, {"implementation_status": "implemented"}
                )
            except ValueError as exc:
                db.rollback()
                channel.send("rejected:" + str(exc))
            else:
                db.commit()
                channel.send("unexpected_answer")
    except Exception as exc:
        channel.send("error:" + str(exc))
    finally:
        engine.dispose()
        channel.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    if "127.0.0.1:15432" not in args.dsn:
        raise ValueError("Only the dedicated loopback test port is allowed")
    schema = "fitagent_followup_race_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    engine = engine_for(args.dsn, schema, "observer")
    user_id, decision_id, plan_id, followup_id = [uuid.uuid4() for _ in range(4)]
    process = None
    parent = None
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            Base.metadata.create_all(connection)
        now = datetime.now(timezone.utc)
        with Session(engine) as db:
            db.add(models.User(id=user_id, email="race@example.test", password_hash="synthetic"))
            db.flush()
            db.add(
                models.AgentDecision(
                    id=decision_id,
                    user_id=user_id,
                    decision_type="training_adjustment",
                    input_summary="synthetic",
                    decision_result="reduce load",
                    reason="synthetic",
                )
            )
            db.flush()
            db.add(
                models.DecisionEvaluationPlan(
                    id=plan_id,
                    user_id=user_id,
                    decision_id=decision_id,
                    evaluation_type="training_adjustment",
                    window_start=now,
                    window_end=now + timedelta(days=7),
                    next_check_at=now,
                )
            )
            db.flush()
            db.add(
                models.DecisionFollowup(
                    id=followup_id,
                    user_id=user_id,
                    evaluation_plan_id=plan_id,
                    question_type="strategy_execution",
                    scheduled_at=now,
                    status="pending",
                )
            )
            db.commit()
        print("Created synthetic-only schema:", schema, flush=True)
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=answer_worker, args=(args.dsn, schema, user_id, followup_id, child)
        )
        process.start()
        child.close()
        assert receive(parent) == "cached_pending"
        with Session(engine) as cancelling:
            lock_plan_owner(cancelling, user_id)
            cancelling.get(models.DecisionFollowup, followup_id).status = "cancelled"
            cancelling.flush()
            parent.send("answer")
            assert receive(parent) == "attempting"
            deadline = time.monotonic() + 15
            waiting = False
            while time.monotonic() < deadline:
                with engine.connect() as connection:
                    waiting = bool(
                        connection.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE "
                                "application_name='fitagent_followup_race_answer' AND wait_event_type='Lock'"
                            )
                        )
                    )
                if waiting:
                    break
                time.sleep(0.1)
            assert waiting, "Answer worker did not show a real database lock wait"
            print("Observed stale answer waiting on owner lock", flush=True)
            cancelling.commit()
        result = receive(parent)
        assert result == "rejected:Decision follow-up is no longer applicable", result
        with Session(engine) as db:
            followup = db.get(models.DecisionFollowup, followup_id)
            assert followup.status == "cancelled"
            assert followup.answer_json == {}
        print(
            "PASS: cancellation preserved; stale answer rejected; no answer persisted", flush=True
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
