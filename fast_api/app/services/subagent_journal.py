"""Owner-scoped durable catalogs using existing AgentRun storage, no migrations.

The caller owns transaction commit. Do not commit an unrelated business session
here; use a dedicated journal transaction when integrating into live execution.
"""

import copy
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.domain_subagents import ROLE_CONFIG
from fast_api.app.services.subagent_runtime import SubagentError

RUN_TYPE = "subagent_catalog"
FIELDS = {
    "child_id",
    "parent_id",
    "domain",
    "depth",
    "mode",
    "status",
    "revision",
    "activation",
    "failure_reason",
}
STATUSES = {"pending", "running", "completed", "failed", "skipped"}


def lease_key(parent_id):
    # Direct UUID projection; collisions conservatively refuse work, never grant access.
    return uuid.UUID(str(parent_id)).int >> 65


def public_catalog(value):
    result = {key: copy.deepcopy(item) for key, item in value.items() if key != "_continuation"}
    state = value.get("_continuation") or {}
    children = value.get("children") or []
    result["continuation_available"] = bool(
        state.get("phase") == "ready"
        and state.get("remaining_calls", 0) > 0
        and len(children) == 1
        and children[0].get("status") == "completed"
        and children[0].get("mode") == "continuable"
        and children[0].get("activation", 3) < 3
    )
    return result


class JournalCheckpoint:
    """Separate SQL transaction per lifecycle change; never commit host session."""

    def __init__(self, engine):
        if engine.dialect.name != "postgresql":
            raise SubagentError("independent_journal_backend_unsupported")
        self.engine = engine
        self.revision = 0
        self.lease = None
        self.parent_id = None

    def close(self):
        if self.lease is not None:
            try:
                self.lease.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": lease_key(self.parent_id)}
                )
                self.lease.commit()
            except Exception:
                # Never return a potentially locked connection to the pool.
                self.lease.invalidate()
                raise
            finally:
                self.lease.close()
                self.lease = None

    def _ensure_lease(self, runtime):
        if self.lease is None:
            self.lease = self.engine.connect()
            self.parent_id = runtime.parent_id
            try:
                acquired = self.lease.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": lease_key(runtime.parent_id)}
                )
                self.lease.commit()
                if not acquired:
                    raise SubagentError("execution_busy")
            except Exception:
                self.lease.invalidate()
                self.lease.close()
                self.lease = None
                raise
        elif self.parent_id != runtime.parent_id:
            raise SubagentError("scope_mismatch")
        else:
            # Refuse a broken lease; do not transparently reconnect and acquire anew.
            if self.lease.invalidated or self.lease.closed:
                raise SubagentError("execution_lease_lost")
            self.lease.execute(text("SELECT 1"))
            self.lease.commit()
        runtime.execution_lease = True

    def __call__(self, runtime):
        self._ensure_lease(runtime)
        with Session(self.engine) as db:
            try:
                # Bound lock waits and statements without changing global settings.
                db.execute(text("SET LOCAL lock_timeout = '2000ms'"))
                db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                value = SubagentJournal(db).save(runtime, expected_revision=self.revision)
                db.commit()
            except Exception as exc:
                db.rollback()
                raise SubagentError("checkpoint_failed") from exc
        self.revision = value["revision"]


class SubagentJournal:
    def __init__(self, db):
        self.db = db

    def _session(self, owner_id, session_id):
        row = self.db.scalar(
            select(models.ConversationSession).where(
                models.ConversationSession.id == session_id,
                models.ConversationSession.user_id == owner_id,
            )
        )
        if row is None:
            raise SubagentError("scope_mismatch")

    def load(self, parent_id, owner_id, session_id):
        self._session(owner_id, session_id)
        row = self.db.scalar(
            select(models.AgentRun)
            .where(
                models.AgentRun.id == parent_id,
                models.AgentRun.user_id == owner_id,
                models.AgentRun.session_id == session_id,
                models.AgentRun.run_type == RUN_TYPE,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise SubagentError("catalog_not_found")
        return copy.deepcopy(row.nodes[0])

    def list_catalogs(self, owner_id, session_id, *, limit=10):
        self._session(owner_id, session_id)
        rows = self.db.scalars(
            select(models.AgentRun)
            .where(
                models.AgentRun.user_id == owner_id,
                models.AgentRun.session_id == session_id,
                models.AgentRun.run_type == RUN_TYPE,
            )
            .order_by(models.AgentRun.started_at.desc(), models.AgentRun.id.desc())
            .limit(max(1, min(limit, 30)))
        ).all()
        return [public_catalog(row.nodes[0]) for row in rows]

    def save(self, runtime, *, expected_revision: int):
        if runtime.owner_id is None or runtime.session_id is None:
            raise SubagentError("unbound_runtime")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise SubagentError("invalid_revision")
        owner = uuid.UUID(runtime.owner_id)
        session = uuid.UUID(runtime.session_id)
        parent = uuid.UUID(runtime.parent_id)
        self._session(owner, session)
        children = runtime.catalog(owner_id=runtime.owner_id, session_id=runtime.session_id)
        if len(children) > 6:
            raise SubagentError("child_limit")
        catalog = []
        for child in children:
            uuid.UUID(child["child_id"])
            if (
                child["parent_id"] != runtime.parent_id
                or child["domain"] not in ROLE_CONFIG
                or child["status"] not in STATUSES
                or child["mode"] not in {"one_shot", "continuable"}
                or child["depth"] != 1
                or not isinstance(child.get("activation"), int)
                or not 1 <= child["activation"] <= 3
                or (child["mode"] == "one_shot" and child["activation"] != 1)
            ):
                raise SubagentError("invalid_checkpoint")
            catalog.append(
                {key: copy.deepcopy(value) for key, value in child.items() if key in FIELDS}
            )
        if len({child["child_id"] for child in catalog}) != len(catalog):
            raise SubagentError("invalid_checkpoint")
        value = {
            "type": "SubagentCatalog",
            "schema_version": 1,
            "revision": expected_revision + 1,
            "parent_id": str(parent),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "children": catalog,
            "execution_lease": {"protocol": 1} if runtime.execution_lease else None,
            "stop_control": {"protocol": 1} if runtime.stop_control else None,
        }
        if runtime.continuation_state is not None:
            serialized = json.dumps(runtime.continuation_state, ensure_ascii=False, default=str)
            if len(serialized.encode("utf-8")) > 131072:
                raise SubagentError("continuation_state_too_large")
            state = json.loads(serialized)
            remaining = state.get("remaining_calls")
            if (
                isinstance(remaining, bool)
                or not isinstance(remaining, int)
                or not 0 <= remaining <= 9
            ):
                raise SubagentError("invalid_checkpoint")
            value["_continuation"] = state
        states = {child["status"] for child in catalog}
        status = (
            "running"
            if not states or states & {"pending", "running"}
            else "completed"
            if states == {"completed"}
            else "skipped"
            if states == {"skipped"}
            else "failed"
        )
        if expected_revision == 0:
            if self.db.get(models.AgentRun, parent) is not None:
                raise SubagentError("checkpoint_conflict")
            try:
                self.db.add(
                    models.AgentRun(
                        id=parent,
                        user_id=owner,
                        session_id=session,
                        run_type=RUN_TYPE,
                        status=status,
                        nodes=[value],
                        summary="Scoped subagent lifecycle catalog",
                    )
                )
                self.db.flush()
            except IntegrityError as exc:
                # The transaction owner must roll back after a racing insert.
                raise SubagentError("checkpoint_conflict") from exc
        else:
            previous = self.load(parent, owner, session)
            if previous["revision"] != expected_revision:
                raise SubagentError("checkpoint_conflict")
            if previous.get("execution_lease") != value["execution_lease"]:
                raise SubagentError("invalid_checkpoint")
            if previous.get("stop_control") != value["stop_control"]:
                raise SubagentError("invalid_checkpoint")
            old_state = previous.get("_continuation")
            if old_state and (
                "_continuation" not in value
                or value["_continuation"]["remaining_calls"] > old_state["remaining_calls"]
            ):
                raise SubagentError("invalid_checkpoint")
            old = {row["child_id"]: row for row in previous["children"]}
            new = {row["child_id"]: row for row in catalog}
            if not old.keys() <= new.keys() or any(
                new[key]["revision"] < row["revision"]
                or (new[key]["revision"] == row["revision"] and new[key] != row)
                or new[key]["domain"] != row["domain"]
                or new[key]["mode"] != row["mode"]
                or new[key]["activation"] < row.get("activation", 1)
                or (
                    new[key]["activation"] != row.get("activation", 1)
                    and not (
                        row["mode"] == "continuable"
                        and row["status"] == "completed"
                        and new[key]["status"] == "pending"
                        and new[key]["activation"] == row.get("activation", 1) + 1
                        and new[key]["revision"] == row["revision"] + 1
                    )
                )
                or (row["status"] in {"failed", "skipped"} and new[key] != row)
                or (
                    row["status"] == "completed"
                    and new[key] != row
                    and not (
                        (
                            new[key]["status"] == "failed"
                            and new[key].get("failure_reason") == "evidence_changed"
                            and new[key]["activation"] == row.get("activation", 1)
                        )
                        or (
                            row["mode"] == "continuable"
                            and new[key]["status"] == "pending"
                            and new[key]["activation"] == row.get("activation", 1) + 1
                        )
                    )
                )
                or (row["status"] == "running" and new[key]["status"] in {"pending", "skipped"})
                for key, row in old.items()
            ):
                raise SubagentError("invalid_checkpoint")
            result = self.db.execute(
                update(models.AgentRun)
                .where(
                    models.AgentRun.id == parent,
                    models.AgentRun.user_id == owner,
                    models.AgentRun.session_id == session,
                    models.AgentRun.run_type == RUN_TYPE,
                    models.AgentRun.nodes == [previous],
                )
                .values(nodes=[value], status=status)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise SubagentError("checkpoint_conflict")
        return copy.deepcopy(value)
