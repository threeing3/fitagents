"""Synthetic PostgreSQL probe, including an abrupt exit of our own helper process."""

import json
import os
import subprocess
import sys
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.subagent_journal import JournalCheckpoint, SubagentJournal
from fast_api.app.services.subagent_recovery import SubagentRecovery
from fast_api.app.services.subagent_runtime import SubagentError, SubagentRuntime
from fast_api.app.services.subagent_sessions import SubagentSessions

URL = "postgresql+psycopg://fitagent_test@127.0.0.1:15432/fitagent_acceptance_20261002"


def main():
    engine = create_engine(URL)
    if len(sys.argv) == 5 and sys.argv[1] == "--crash-child":
        parent, owner, session = sys.argv[2:]
        runtime = SubagentRuntime(parent_id=parent)
        runtime.bind(owner, session)
        runtime.checkpoint = JournalCheckpoint(engine)
        child = runtime.start("training")
        runtime.transition(child["child_id"], "running")
        os._exit(0)  # Our own synthetic helper only; deliberately bypass cleanup.
    owner, session = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        db.add(
            models.User(
                id=owner, email=f"recovery-{owner}@example.com", password_hash="synthetic-disabled"
            )
        )
        db.flush()
        db.add(
            models.ConversationSession(id=session, user_id=owner, title="Synthetic recovery probe")
        )
        db.commit()
    runtime = SubagentRuntime()
    runtime.bind(str(owner), str(session))
    checkpoint = JournalCheckpoint(engine)
    runtime.checkpoint = checkpoint
    try:
        child = runtime.start("training")
        runtime.transition(child["child_id"], "running")
        with Session(engine) as db:
            db.add(
                models.IdempotencyRecord(
                    user_id=owner,
                    operation="subagent_turn",
                    idempotency_key="synthetic-live-receipt",
                    status="processing",
                    request_json={"session_id": str(session)},
                    response_json={"parent_id": runtime.parent_id},
                )
            )
            db.commit()
            try:
                SubagentSessions(db, None).reconcile_request(
                    owner, session, "synthetic-live-receipt", checkpoint.revision
                )
                raise AssertionError("Live receipt cannot be finalized")
            except SubagentError as exc:
                assert str(exc) == "execution_busy", exc
                db.rollback()
            try:
                SubagentRecovery(db).reconcile(
                    uuid.UUID(runtime.parent_id), owner, session, checkpoint.revision
                )
                raise AssertionError("Active lease must refuse reconciliation")
            except SubagentError as exc:
                assert str(exc) == "execution_busy"
                db.rollback()
    finally:
        runtime.close_active("consumer_closed")
        checkpoint.close()
    crashed_parent = uuid.uuid4()
    subprocess.run(
        [sys.executable, __file__, "--crash-child", str(crashed_parent), str(owner), str(session)],
        check=True,
        timeout=30,
    )
    with Session(engine) as db:
        journal = SubagentJournal(db)
        before = journal.load(crashed_parent, owner, session)
        assert before["children"][0]["status"] == "running"
        revision = before["revision"]
        db.add(
            models.IdempotencyRecord(
                user_id=owner,
                operation="subagent_turn",
                idempotency_key="synthetic-crashed-receipt",
                status="processing",
                request_json={"session_id": str(session)},
                response_json={"parent_id": str(crashed_parent)},
            )
        )
        db.commit()
        result = SubagentRecovery(db).reconcile(crashed_parent, owner, session, revision)
        assert result["changed"] and not result["requeued"]
        db.commit()
        after = SubagentJournal(db).load(crashed_parent, owner, session)
        finalized = SubagentSessions(db, None).reconcile_request(
            owner, session, "synthetic-crashed-receipt", after["revision"]
        )
        assert finalized["status"] == "recorded"
        assert finalized["result"]["reconciled_receipt"]
        assert finalized["result"]["model_called"] is None
        assert finalized["result"]["status"] == "failed"
        repeated = SubagentSessions(db, None).reconcile_request(
            owner, session, "synthetic-crashed-receipt", after["revision"]
        )
        assert repeated == finalized
    with Session(engine) as db:
        after = SubagentJournal(db).load(crashed_parent, owner, session)
        assert after["children"][0]["failure_reason"] == "execution_interrupted"
        repeat = SubagentRecovery(db).reconcile(crashed_parent, owner, session, after["revision"])
        assert not repeat["changed"] and not repeat["requeued"]
        db.commit()
        try:
            SubagentRecovery(db).reconcile(crashed_parent, owner, session, revision)
            raise AssertionError("Old revision must be refused")
        except SubagentError as exc:
            assert str(exc) == "checkpoint_conflict"
            db.rollback()
    completed_runtime = SubagentRuntime()
    completed_runtime.bind(str(owner), str(session))
    completed_checkpoint = JournalCheckpoint(engine)
    completed_runtime.checkpoint = completed_checkpoint
    try:
        completed_child = completed_runtime.start("training")
        completed_runtime.transition(completed_child["child_id"], "running")
        completed_runtime.transition(completed_child["child_id"], "completed")
    finally:
        completed_checkpoint.close()
    with Session(engine) as db:
        db.add(
            models.IdempotencyRecord(
                user_id=owner,
                operation="subagent_turn",
                idempotency_key="synthetic-undelivered-receipt",
                status="processing",
                request_json={"session_id": str(session)},
                response_json={"parent_id": completed_runtime.parent_id},
            )
        )
        db.commit()
        try:
            SubagentSessions(db, None).reconcile_request(
                owner, session, "synthetic-undelivered-receipt", completed_checkpoint.revision
            )
            raise AssertionError("Undelivered success must remain unconfirmed")
        except SubagentError as exc:
            assert str(exc) == "result_delivery_unconfirmed", exc
            db.rollback()
    print(
        json.dumps(
            {
                "status": "passed",
                "active_execution_refused": True,
                "own_helper_abrupt_exit_verified": True,
                "repeated_recovery_no_retry": True,
                "old_revision_refused": True,
                "orphan_receipt_finalized_without_retry": True,
                "undelivered_success_refused": True,
                "model_called": False,
                "synthetic_session": str(session),
                "reconciled_parent": str(crashed_parent),
            }
        )
    )
    engine.dispose()


if __name__ == "__main__":
    main()
