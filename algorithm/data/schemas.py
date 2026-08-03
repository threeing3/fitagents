"""Versioned, JSON-serialisable contracts used by the algorithm layer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar


SCHEMA_VERSION = "2026-08-01"
VALID_SOURCES = {
    "agent_trace",
    "rule_generated",
    "expert_labeled",
    "teacher_generated",
    "synthetic",
    "simulated_outcome",
    "seed_eval",
}
VALID_SPLITS = {"train", "validation", "test", "quarantine"}


def stable_hash(value: str, salt: str = "") -> str:
    """Return a stable non-reversible identifier for split/grouping only."""

    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:24]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OutcomeLabel:
    accepted_by_user: bool | None = None
    implementation_status: str | None = None
    adherence_7d: float | None = None
    negative_feedback: bool | None = None
    safety_status: str | None = None
    outcome_status: str | None = None
    label_confidence: float = 0.0
    label_source: str = "unknown"


@dataclass
class TrainingExample:
    """Canonical example shared by export, validation, and training builders."""

    example_id: str
    task_type: str
    user_message: str
    user_hash: str = "unknown"
    session_hash: str = "unknown"
    profile_context: dict[str, Any] = field(default_factory=dict)
    retrieved_context: dict[str, Any] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    assistant_response: str = ""
    intent_label: str | None = None
    risk_label: str | None = None
    quality_labels: dict[str, float | int | str | bool] = field(default_factory=dict)
    guardrail_result: dict[str, Any] = field(default_factory=dict)
    outcome: OutcomeLabel | None = None
    model_version: str = "unknown"
    prompt_version: str = "unknown"
    rule_version: str = "unknown"
    source: str = "agent_trace"
    split: str = "quarantine"
    schema_version: str = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now_iso)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.example_id:
            errors.append("example_id is required")
        if not self.task_type:
            errors.append("task_type is required")
        if not self.user_message.strip():
            errors.append("user_message is required")
        if self.source not in VALID_SOURCES:
            errors.append(f"unknown source: {self.source}")
        if self.split not in VALID_SPLITS:
            errors.append(f"unknown split: {self.split}")
        if not 0.0 <= (self.outcome.label_confidence if self.outcome else 0.0) <= 1.0:
            errors.append("outcome.label_confidence must be in [0, 1]")
        return errors

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TrainingExample":
        raw = dict(payload)
        outcome = raw.get("outcome")
        if isinstance(outcome, dict):
            raw["outcome"] = OutcomeLabel(**outcome)
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass
class ToolDecisionExample:
    user_message: str
    context_summary: dict[str, Any] = field(default_factory=dict)
    intent: str = "general_chat"
    risk_level: str = "low"
    selected_tools: list[str] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    plan_valid: bool = False
    tool_execution_result: dict[str, Any] = field(default_factory=dict)
    source: str = "agent_trace"
    example_id: str = ""
    split: str = "quarantine"
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.user_message.strip():
            errors.append("user_message is required")
        if self.risk_level not in {"low", "medium", "high", "critical", "unknown"}:
            errors.append(f"unknown risk_level: {self.risk_level}")
        if not isinstance(self.selected_tools, list) or not all(isinstance(item, str) and item for item in self.selected_tools):
            errors.append("selected_tools must be a list of non-empty strings")
        if not isinstance(self.tool_sequence, list) or not all(isinstance(item, str) and item for item in self.tool_sequence):
            errors.append("tool_sequence must be a list of non-empty strings")
        if any(item not in self.selected_tools for item in self.tool_sequence):
            errors.append("tool_sequence contains a tool missing from selected_tools")
        if self.source not in VALID_SOURCES:
            errors.append(f"unknown source: {self.source}")
        if self.split not in VALID_SPLITS:
            errors.append(f"unknown split: {self.split}")
        return errors


@dataclass
class PreferencePair:
    prompt: str
    chosen: str
    rejected: str
    preference_reason: list[str] = field(default_factory=list)
    feedback_source: str = "unknown"
    guardrail_comparison: dict[str, Any] = field(default_factory=dict)
    business_outcome_comparison: dict[str, Any] = field(default_factory=dict)
    example_id: str = ""
    source: str = "agent_trace"
    split: str = "quarantine"
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.prompt.strip():
            errors.append("prompt is required")
        if not self.chosen.strip():
            errors.append("chosen is required")
        if not self.rejected.strip():
            errors.append("rejected is required")
        if self.chosen.strip() == self.rejected.strip():
            errors.append("chosen and rejected must differ")
        if self.source not in VALID_SOURCES:
            errors.append(f"unknown source: {self.source}")
        if self.split not in VALID_SPLITS:
            errors.append(f"unknown split: {self.split}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetManifest:
    dataset_name: str
    dataset_version: str
    schema_version: str = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    source_files: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    split_counts: dict[str, int] = field(default_factory=dict)
    source_counts: dict[str, int] = field(default_factory=dict)
    user_count: int = 0
    scenario_count: int = 0
    validation_errors: int = 0
    code_version: str = "working-tree"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
