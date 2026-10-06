"""Explicit reconciliation of unleased read-only runs; never retry a child."""

import copy
from datetime import datetime, timezone

from sqlalchemy import text, update

from fast_api.app.db import models
from fast_api.app.services.subagent_journal import RUN_TYPE, SubagentJournal, lease_key
from fast_api.app.services.subagent_runtime import SubagentError


class SubagentRecovery:
    def __init__(self, db):
        self.db = db

    def reconcile(self, parent_id, owner_id, session_id, expected_revision):
        if self.db.get_bind().dialect.name == "postgresql":
            self.db.execute(text("SET LOCAL lock_timeout = '2000ms'"))
            self.db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        previous = SubagentJournal(self.db).load(parent_id, owner_id, session_id)
        if self.db.get_bind().dialect.name != "postgresql" or previous.get("execution_lease") != {
            "protocol": 1
        }:
            raise SubagentError("unsupported_liveness_evidence")
        if previous["revision"] != expected_revision:
            raise SubagentError("checkpoint_conflict")
        if not self.db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lease_key(parent_id)}
        ):
            raise SubagentError("execution_busy")
        # Re-read only after lock acquisition: another reconciliation may have committed.
        current = SubagentJournal(self.db).load(parent_id, owner_id, session_id)
        if current["revision"] != expected_revision:
            raise SubagentError("checkpoint_conflict")
        active = [row for row in current["children"] if row["status"] in {"pending", "running"}]
        if not active:
            return {"changed": False, "requeued": False, "revision": current["revision"]}
        value = copy.deepcopy(current)
        for child in value["children"]:
            if child["status"] in {"pending", "running"}:
                child.update(
                    status="failed",
                    failure_reason="execution_interrupted",
                    revision=child["revision"] + 1,
                )
        value.update(
            revision=current["revision"] + 1,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            reconciliation={"no_reexecution": True, "liveness_lock_acquired": True},
        )
        result = self.db.execute(
            update(models.AgentRun)
            .where(
                models.AgentRun.id == parent_id,
                models.AgentRun.user_id == owner_id,
                models.AgentRun.session_id == session_id,
                models.AgentRun.run_type == RUN_TYPE,
                models.AgentRun.nodes == [current],
            )
            .values(nodes=[value], status="failed")
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise SubagentError("checkpoint_conflict")
        return {"changed": True, "requeued": False, "revision": value["revision"]}
