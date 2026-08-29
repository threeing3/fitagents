"""Contracts for auditable intent-language augmentation requests and outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

VALID_AUGMENTATION_SOURCES = {"human_authored", "teacher_generated", "rule_generated"}
VALID_LANGUAGE_FACTORS = {
    "colloquial",
    "ellipsis",
    "negation",
    "context_conflict",
    "intent_order",
    "implicit_request",
    "adversarial_noise",
}


@dataclass(frozen=True)
class IntentAugmentationRequest:
    request_id: str
    primary_intent: str
    secondary_intents: tuple[str, ...]
    semantic_brief: str
    language_factor: str
    requested_source: str
    split_target: str
    prompt_version: str = "intent-augmentation-v1"
    development_text_access: bool = False
    fixed_test_text_access: bool = False

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.request_id:
            errors.append("request_id is required")
        if self.language_factor not in VALID_LANGUAGE_FACTORS:
            errors.append(f"unknown language_factor: {self.language_factor}")
        if self.requested_source not in VALID_AUGMENTATION_SOURCES:
            errors.append(f"unknown requested_source: {self.requested_source}")
        if self.split_target not in {"train", "validation"}:
            errors.append("split_target must be train or validation")
        if self.development_text_access or self.fixed_test_text_access:
            errors.append("evaluation text access is forbidden during augmentation")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntentAugmentationOutput:
    request_id: str
    user_message: str
    primary_intent: str
    secondary_intents: tuple[str, ...]
    source: str
    generator_id: str
    prompt_version: str
    human_review_status: str = "pending"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.user_message.strip():
            errors.append("user_message is required")
        if self.source not in VALID_AUGMENTATION_SOURCES:
            errors.append(f"unknown source: {self.source}")
        if self.source == "teacher_generated" and not self.generator_id.strip():
            errors.append("teacher_generated output requires generator_id")
        if self.human_review_status not in {"pending", "approved", "rejected"}:
            errors.append("unknown human_review_status")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
