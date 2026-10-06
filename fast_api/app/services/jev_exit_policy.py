"""Selective Jev acceptance. All routes disabled until explicitly calibrated."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from fast_api.app.services.intent_decision import IntentDecision
from fast_api.app.services.jev_intent_client import TASKS, JevIntentClient, JevIntentResult


@dataclass(frozen=True)
class JevCalibration:
    """Caller-owned reviewed calibration, not proof of semantic correctness."""

    reference: str
    model: str
    route_thresholds: dict[str, float] = field(default_factory=dict)
    minimum_confidence: float = 0.95
    minimum_margin: float = 0.8
    maximum_risk_probability: float = 0.01


@dataclass(frozen=True)
class JevExitAssessment:
    outcome: str
    reason: str
    intent: str | None = None

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "reason": self.reason, "intent": self.intent}


class JevExitPolicy:
    """V1 accepts only isolated read-only tasks; complex and write tasks escalate."""

    READ_ONLY = frozenset({"concept_explanation", "memory_query"})

    def __init__(self, calibration: JevCalibration | None = None):
        if calibration is not None:
            values = [
                calibration.minimum_confidence,
                calibration.minimum_margin,
                calibration.maximum_risk_probability,
                *calibration.route_thresholds.values(),
            ]
            if (
                not calibration.reference.strip()
                or calibration.model != JevIntentClient.MODEL
                or not set(calibration.route_thresholds) <= self.READ_ONLY
                or any(not JevIntentClient._number(value) for value in values)
            ):
                raise ValueError("Invalid read-only calibration")
        self.calibration = calibration

    def assess(
        self, message: str, result: JevIntentResult, rule: IntentDecision
    ) -> JevExitAssessment:
        def reject(reason: str) -> JevExitAssessment:
            return JevExitAssessment("escalate", reason)

        if not result.succeeded:
            return reject(result.status)
        if self.calibration is None:
            return reject("calibration_missing")
        calibration = self.calibration
        if result.model != calibration.model:
            return reject("model_version_mismatch")
        # Revalidate injected clients as well as the normal HTTP client.
        body = {
            "model": result.model,
            "answers": {
                task: {
                    "type": "choice",
                    "choice": result.task_states.get(task),
                    "confidence": result.confidence.get(task),
                    "probabilities": result.probabilities.get(task),
                }
                for task in TASKS
            },
            "usage": result.usage,
        }
        body["answers"]["current_risk"] = {"type": "noul", "noul": result.current_risk_probability}
        if not JevIntentClient.parse_response(body).succeeded:
            return reject("invalid_candidate_contract")
        if rule.risk_level != "low" or "injury_or_risk" in [
            rule.primary_intent,
            *rule.secondary_intents,
        ]:
            return reject("rule_risk_floor")
        if result.current_risk_probability > calibration.maximum_risk_probability:
            return reject("risk_requires_host_review")
        if re.search(r"那个|第二个|照旧|刚才|上回那套|之前那个|改成|纠正|不要|别", message):
            return reject("complex_reference_or_constraint")
        requested = [task for task, state in result.task_states.items() if state == "requested"]
        if len(requested) != 1:
            return reject("requires_complete_multitask_review")
        intent = requested[0]
        if intent not in calibration.route_thresholds:
            return reject("route_not_calibrated")
        for task in TASKS:
            state = result.task_states[task]
            if state not in {"requested", "absent"}:
                return reject("nontrivial_task_state")
            probabilities = result.probabilities[task]
            ordered = sorted(probabilities.values(), reverse=True)
            if (
                result.confidence[task] < calibration.minimum_confidence
                or probabilities[state] < calibration.route_thresholds[intent]
                or ordered[0] - ordered[1] < calibration.minimum_margin
            ):
                return reject("uncertain_task_presence_or_absence")
        if rule.primary_intent not in {"general_chat", intent} or rule.secondary_intents:
            return reject("rule_candidate_conflict")
        if not math.isfinite(result.current_risk_probability):
            return reject("invalid_risk")
        return JevExitAssessment("accept", "calibrated_read_only_complete_task", intent)
