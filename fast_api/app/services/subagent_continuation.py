"""Scoped continuable child kernel; no raw history or automatic failure retries."""

import copy
import json
import time
import uuid

from fast_api.app.services.domain_subagents import ROLE_CONFIG, DomainSubagents
from fast_api.app.services.execution_events import execution_event
from fast_api.app.services.subagent_runtime import SubagentError


class ContinuableChild:
    """Three explicit activations, nine total model calls, new evidence each turn."""

    def __init__(self, provider, reader_factory, *, owner_id, session_id, role):
        if role not in ROLE_CONFIG:
            raise SubagentError("invalid_roles")
        self.role = role
        self.reader_factory = reader_factory
        self.worker = DomainSubagents(provider, lambda role, tool: {})
        self.worker.runtime.allow_continuation = True
        self.worker.runtime.bind(str(owner_id), str(session_id))
        self.child_id = None
        self.previous_result = None
        self.observations = {}
        self._active = False
        self._delivered_activation = 0
        self.checkpoint = None

    def attach_checkpoint(self, checkpoint):
        self.checkpoint = checkpoint

        def save(runtime):
            runtime.continuation_state = self.export_state()
            checkpoint(runtime)

        self.worker.runtime.checkpoint = save
        self.worker.on_budget_change = self.worker.runtime._checkpoint

    def export_state(self):
        rows = list(self.worker.runtime.children.values())
        row = rows[0] if rows else None
        ready = bool(
            row and row["status"] == "completed" and row["activation"] == self._delivered_activation
        )
        return json.loads(
            json.dumps(
                {
                    "schema_version": 1,
                    "role": self.role,
                    "child_id": row["child_id"] if row else None,
                    "remaining_calls": self.worker.remaining_calls,
                    "phase": "ready" if ready else "unconfirmed",
                    "delivered_activation": self._delivered_activation,
                    "previous_result": self.previous_result,
                    "observations": self.observations,
                },
                ensure_ascii=False,
                default=str,
            )
        )

    @classmethod
    def restore(cls, provider, reader_factory, *, db, parent_id, owner_id, session_id):
        from fast_api.app.services.subagent_journal import SubagentJournal

        saved = SubagentJournal(db).load(
            uuid.UUID(str(parent_id)), uuid.UUID(str(owner_id)), uuid.UUID(str(session_id))
        )
        state = saved.get("_continuation")
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or state.get("phase") != "ready"
        ):
            raise SubagentError("continuation_not_ready")
        required = {
            "role",
            "child_id",
            "remaining_calls",
            "delivered_activation",
            "previous_result",
            "observations",
        }
        if not required <= state.keys():
            raise SubagentError("invalid_checkpoint")
        remaining = state["remaining_calls"]
        if isinstance(remaining, bool) or not isinstance(remaining, int) or not 0 <= remaining <= 9:
            raise SubagentError("invalid_checkpoint")
        if not isinstance(state["previous_result"], dict) or not isinstance(
            state["observations"], dict
        ):
            raise SubagentError("invalid_checkpoint")
        children = saved["children"]
        if (
            len(children) != 1
            or children[0]["mode"] != "continuable"
            or children[0]["status"] != "completed"
        ):
            raise SubagentError("continuation_not_ready")
        row = children[0]
        if (
            state["child_id"] != row["child_id"]
            or state["role"] != row["domain"]
            or state["delivered_activation"] != row["activation"]
            or state["previous_result"].get("child_id") != row["child_id"]
            or state["previous_result"].get("activation") != row["activation"]
            or state["previous_result"].get("status") != "completed"
        ):
            raise SubagentError("invalid_checkpoint")
        instance = cls(
            provider, reader_factory, owner_id=owner_id, session_id=session_id, role=state["role"]
        )
        instance.worker.runtime.parent_id = str(uuid.UUID(str(parent_id)))
        instance.worker.runtime.children = {row["child_id"]: copy.deepcopy(row)}
        instance.worker.runtime.execution_lease = saved.get("execution_lease") == {"protocol": 1}
        instance.child_id = row["child_id"]
        instance.worker.remaining_calls = state["remaining_calls"]
        instance.previous_result = state["previous_result"]
        instance.observations = state["observations"]
        instance._delivered_activation = state["delivered_activation"]
        return instance, saved["revision"]

    async def run(self, owner_id, session_id, message, *, expected_revision=None):
        self.worker.runtime.catalog(owner_id=str(owner_id), session_id=str(session_id))
        if self._active:
            raise SubagentError("concurrent_activation_not_supported")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 4000:
            raise SubagentError("invalid_task")
        if isinstance(expected_revision, bool) or (
            self.child_id and not isinstance(expected_revision, int)
        ):
            raise SubagentError("invalid_revision")
        if (
            self.child_id
            and self.worker.runtime.children[self.child_id]["revision"] != expected_revision
        ):
            raise SubagentError("checkpoint_conflict")
        if self.worker.remaining_calls <= 0:
            raise SubagentError("shared_budget_exhausted")
        self._active = True
        stream = None
        try:
            self.worker.reader = self.reader_factory(message)
            self.worker.deadline = time.monotonic() + 90
            changed = any(
                json.loads(json.dumps(self.worker.reader(self.role, tool), default=str)) != value
                for tool, value in self.observations.items()
            )
            handoff = (
                [] if changed or not self.previous_result else [copy.deepcopy(self.previous_result)]
            )
            if changed:
                yield execution_event(
                    "subagent.handoff", "blocked", "上一轮证据已变化，旧交接被丢弃；本轮重新取证。"
                )
            stream = self.worker.run(
                {},
                message,
                roles=[self.role],
                mode="continuable",
                handoff=handoff,
                resume_child_id=self.child_id,
                expected_revision=expected_revision,
            )
            async for entry in stream:
                if entry["type"] == "domain_result":
                    row = entry["results"][0]
                    self.child_id = row["child_id"]
                    self.observations = json.loads(
                        json.dumps(row.pop("observations", {}), default=str)
                    )
                    self.previous_result = (
                        copy.deepcopy(row) if row["status"] == "completed" else None
                    )
                    self._delivered_activation = self.worker.runtime.children[self.child_id][
                        "activation"
                    ]
                    self.worker.runtime._checkpoint()
                    yield entry
                else:
                    # Preserve identity even if the consumer closes before final delivery.
                    self.child_id = entry.get("details", {}).get("child_id", self.child_id)
                    yield entry
        finally:
            try:
                if stream is not None:
                    await stream.aclose()
            finally:
                if self.child_id is None and self.worker.runtime.children:
                    self.child_id = next(iter(self.worker.runtime.children))
                self._active = False
