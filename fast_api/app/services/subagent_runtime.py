"""Scoped child lifecycle with opt-in continuation. No global/write capabilities."""

import copy
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class SubagentError(ValueError):
    """Stable error code; never include private prompts or provider errors."""


@dataclass
class SubagentRuntime:
    parent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    owner_id: str | None = None
    session_id: str | None = None
    max_children: int = 6
    max_depth: int = 1
    execution_lease: bool = False
    stop_control: bool = False
    allow_continuation: bool = False
    continuation_state: dict[str, Any] | None = None
    children: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkpoint: Callable[["SubagentRuntime"], None] | None = field(default=None, repr=False)

    def _checkpoint(self, child_id=None):
        if child_id is not None:
            from fast_api.app.services.model_call_records import record_child_state

            record_child_state(self.parent_id, self.children[child_id])
        if self.checkpoint is not None:
            self.checkpoint(self)

    def bind(self, owner_id: str, session_id: str):
        if self.children or not owner_id or not session_id:
            raise SubagentError("invalid_scope_binding")
        self.owner_id, self.session_id = str(owner_id), str(session_id)

    def start(self, role: str, *, mode: str = "one_shot", depth: int = 1) -> dict[str, Any]:
        if mode != "one_shot" and not (mode == "continuable" and self.allow_continuation):
            raise SubagentError("unsupported_capability")
        if depth != 1 or depth > self.max_depth:
            raise SubagentError("depth_limit")
        if any(row["status"] in {"pending", "running"} for row in self.children.values()):
            raise SubagentError("concurrent_activation_not_supported")
        if len(self.children) >= self.max_children:
            raise SubagentError("child_limit")
        child_id = str(uuid.uuid4())
        row = {
            "child_id": child_id,
            "parent_id": self.parent_id,
            "domain": role,
            "depth": depth,
            "mode": mode,
            "status": "pending",
            "revision": 1,
            "activation": 1,
            "created_at": time.monotonic(),
            "finished_at": None,
        }
        self.children[child_id] = row
        self._checkpoint(child_id)
        return copy.deepcopy(row)

    def resume(self, child_id: str, expected_revision: int):
        row = self.children.get(child_id)
        if row is None or not self.allow_continuation or row["mode"] != "continuable":
            raise SubagentError("unsupported_capability")
        if row["revision"] != expected_revision:
            raise SubagentError("checkpoint_conflict")
        if row["status"] != "completed":
            raise SubagentError("child_not_ready")
        if row["activation"] >= 3:
            raise SubagentError("activation_limit")
        if any(child["status"] in {"pending", "running"} for child in self.children.values()):
            raise SubagentError("concurrent_activation_not_supported")
        row.update(
            status="pending",
            activation=row["activation"] + 1,
            revision=row["revision"] + 1,
            finished_at=None,
        )
        self._checkpoint(child_id)
        return copy.deepcopy(row)

    def transition(self, child_id: str, status: str, *, reason: str | None = None):
        row = self.children[child_id]
        if row["status"] in {"completed", "failed", "skipped"}:
            raise SubagentError("terminal_child")
        allowed = {"pending": {"running", "failed", "skipped"}, "running": {"completed", "failed"}}
        if status not in allowed[row["status"]]:
            raise SubagentError("invalid_transition")
        row["status"] = status
        row["revision"] += 1
        if reason:
            row["failure_reason"] = reason
        if status in {"completed", "failed", "skipped"}:
            row["finished_at"] = time.monotonic()
        self._checkpoint(child_id)

    def close_active(self, reason: str):
        for child_id, row in self.children.items():
            if row["status"] in {"pending", "running"}:
                self.transition(child_id, "failed", reason=reason)

    def invalidate(self, child_id: str):
        row = self.children[child_id]
        if row["status"] != "completed":
            raise SubagentError("invalid_transition")
        row.update(status="failed", failure_reason="evidence_changed", revision=row["revision"] + 1)
        self._checkpoint(child_id)

    def catalog(self, *, owner_id: str | None = None, session_id: str | None = None):
        if self.owner_id is not None and (str(owner_id), str(session_id)) != (
            self.owner_id,
            self.session_id,
        ):
            raise SubagentError("scope_mismatch")
        return copy.deepcopy(list(self.children.values()))


def parse_child_response(content: Any) -> dict[str, Any]:
    """Reject duplicate keys, non-JSON numerics, blocks and oversized responses."""
    if not isinstance(content, str) or len(content.encode("utf-8")) > 32768:
        raise SubagentError("invalid_response_size_or_type")

    def pairs(entries):
        data = {}
        for key, value in entries:
            if key in data:
                raise SubagentError("duplicate_json_key")
            data[key] = value
        return data

    def invalid_constant(_value):
        raise SubagentError("invalid_result")

    try:
        value = json.loads(content, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SubagentError("invalid_result") from exc
    if not isinstance(value, dict):
        raise SubagentError("invalid_result")
    return value
