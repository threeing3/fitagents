from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryVerificationIssue:
    issue_id: str
    severity: str
    message: str
    candidate_index: int | None = None
    action: str = "review"
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "severity": self.severity,
            "message": self.message,
            "candidate_index": self.candidate_index,
            "action": self.action,
            "evidence": self.evidence,
        }


@dataclass
class MemoryVerificationResult:
    passed: bool
    accepted_candidates: list[dict[str, Any]] = field(default_factory=list)
    accepted_corrections: list[dict[str, Any]] = field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = field(default_factory=list)
    issues: list[MemoryVerificationIssue] = field(default_factory=list)
    repair_actions: list[str] = field(default_factory=list)
    profile_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "accepted_count": len(self.accepted_candidates),
            "accepted_candidates": self.accepted_candidates,
            "accepted_corrections": self.accepted_corrections,
            "rejected_count": len(self.rejected_candidates),
            "rejected_candidates": self.rejected_candidates,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
            "repair_actions": self.repair_actions,
            "profile_snapshot": self.profile_snapshot,
        }


class MemoryVerifier:
    """Rule-first verifier for long-term memory writes.

    The goal is to prevent memory pollution before facts become durable. This is
    intentionally deterministic: the LLM may suggest memories, but persistent
    memory writes must pass profile-consistency and correction checks first.
    """

    INJURY_NEGATION_TERMS = [
        "没有伤",
        "没有肩伤",
        "没肩伤",
        "无伤",
        "没受伤",
        "没有说过",
        "否认",
        "no injury",
        "not injured",
    ]
    INJURY_ASSERTION_TERMS = ["伤", "痛", "疼", "不适", "受伤", "康复", "injury", "pain"]
    BODY_PART_TERMS = ["肩", "肩部", "右肩", "左肩", "shoulder"]
    STABLE_MEMORY_TYPES = {"stable_preference", "medical_context", "nutrition_habit", "training_performance", "correction"}
    TEMPORARY_MEMORY_TYPES = {"recent_state", "adjustment"}

    def verify(
        self,
        candidates: list[dict[str, Any]],
        corrections: list[dict[str, Any]],
        profile_snapshot: dict[str, Any] | None = None,
        message: str = "",
    ) -> MemoryVerificationResult:
        profile_snapshot = profile_snapshot or {}
        message_text = message or ""
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        issues: list[MemoryVerificationIssue] = []
        repair_actions: list[str] = []

        normalized_corrections = [
            correction for correction in corrections if isinstance(correction, dict)
        ]
        injury_removal = self._has_injury_removal(normalized_corrections, message_text)
        profile_injuries = [str(item).lower() for item in (profile_snapshot.get("injuries") or [])]

        for index, candidate in enumerate(candidates):
            candidate = dict(candidate or {})
            memory_type = str(candidate.get("memory_type") or "")
            content = str(candidate.get("content") or "")
            metadata = dict(candidate.get("memory_metadata") or {})
            lowered = content.lower()
            reject_reasons: list[MemoryVerificationIssue] = []

            if not content.strip():
                reject_reasons.append(
                    MemoryVerificationIssue(
                        "empty_memory_content",
                        "error",
                        "候选记忆内容为空，不能写入长期记忆。",
                        index,
                        action="reject",
                    )
                )

            if injury_removal and self._is_injury_or_risk_candidate(memory_type, content, metadata):
                reject_reasons.append(
                    MemoryVerificationIssue(
                        "contradicts_injury_correction",
                        "error",
                        "用户正在纠正伤病信息，不能同时写入新的伤病/风险记忆。",
                        index,
                        action="reject",
                        evidence={"corrections": normalized_corrections},
                    )
                )

            if memory_type == "risk_signal":
                if self._only_body_part_without_injury_context(lowered, message_text):
                    reject_reasons.append(
                        MemoryVerificationIssue(
                            "body_part_without_injury_context",
                            "error",
                            "只出现身体部位或动作名，不能直接当成伤病风险写入。",
                            index,
                            action="reject",
                            evidence={"content": content},
                        )
                    )

            if memory_type == "stable_preference" and self._looks_temporary_state(lowered):
                metadata["category"] = metadata.get("category") or "recent_state"
                candidate["memory_type"] = "recent_state"
                candidate["importance"] = min(float(candidate.get("importance") or 0.6), 0.7)
                candidate["memory_metadata"] = {
                    **metadata,
                    "memory_verify_repair": "downgraded_stable_preference_to_recent_state",
                }
                issues.append(
                    MemoryVerificationIssue(
                        "temporary_state_as_stable_preference",
                        "warn",
                        "候选记忆像临时状态，不应作为稳定偏好保存，已降级为 recent_state。",
                        index,
                        action="downgrade",
                    )
                )
                repair_actions.append("downgrade_stable_preference_to_recent_state")

            if memory_type == "training_performance" and self._mentions_negated_injury(lowered):
                candidate["memory_metadata"] = {
                    **metadata,
                    "memory_verify_note": "contains_injury_negation_not_injury_fact",
                }

            if "shoulder" in profile_injuries and self._mentions_negated_injury(lowered):
                reject_reasons.append(
                    MemoryVerificationIssue(
                        "profile_conflict_existing_shoulder_injury",
                        "error",
                        "canonical profile 中仍有 shoulder，但用户文本否认肩伤，需要先纠正档案。",
                        index,
                        action="reject",
                        evidence={"profile_injuries": profile_snapshot.get("injuries") or []},
                    )
                )

            if reject_reasons:
                rejected.append({**candidate, "reasons": [issue.to_dict() for issue in reject_reasons]})
                issues.extend(reject_reasons)
                repair_actions.extend(issue.action for issue in reject_reasons if issue.action != "review")
                continue

            accepted.append(candidate)

        passed = not any(issue.severity == "error" for issue in issues)
        return MemoryVerificationResult(
            passed=passed,
            accepted_candidates=accepted,
            accepted_corrections=normalized_corrections,
            rejected_candidates=rejected,
            issues=issues,
            repair_actions=sorted(set(repair_actions)),
            profile_snapshot=profile_snapshot,
        )

    def _has_injury_removal(self, corrections: list[dict[str, Any]], message: str) -> bool:
        if any(
            correction.get("field") == "injuries"
            and correction.get("action") in {"remove", "clear"}
            for correction in corrections
        ):
            return True
        return self._mentions_negated_injury(message.lower())

    def _mentions_negated_injury(self, lowered: str) -> bool:
        return any(term in lowered for term in self.INJURY_NEGATION_TERMS)

    def _mentions_injury(self, lowered: str) -> bool:
        return any(term in lowered for term in self.INJURY_ASSERTION_TERMS) or any(
            term in lowered for term in self.BODY_PART_TERMS
        )

    def _is_injury_or_risk_candidate(self, memory_type: str, content: str, metadata: dict[str, Any]) -> bool:
        if memory_type == "risk_signal":
            return True
        category = str(metadata.get("category") or metadata.get("risk_type") or "").lower()
        if category in {"risk", "injury", "injuries", "pain", "rehab", "joint_pain", "sports_injury"}:
            return True
        tags = [str(tag).lower() for tag in (metadata.get("tags") or []) if tag is not None]
        if any(tag in {"injury", "pain", "rehab", "shoulder_injury"} for tag in tags):
            return True
        lowered = content.lower()
        return "肩伤" in lowered or "shoulder injury" in lowered or "肩部受伤" in lowered

    def _only_body_part_without_injury_context(self, lowered: str, original_message: str) -> bool:
        mentions_body_part = any(term in lowered for term in self.BODY_PART_TERMS)
        mentions_injury_context = any(term in lowered for term in self.INJURY_ASSERTION_TERMS)
        negates_injury = self._mentions_negated_injury(lowered) or self._mentions_negated_injury(original_message.lower())
        return mentions_body_part and (not mentions_injury_context or negates_injury)

    def _looks_temporary_state(self, lowered: str) -> bool:
        return any(
            term in lowered
            for term in ["今天", "昨晚", "最近", "这周", "疲劳", "酸痛", "睡眠", "状态", "today", "recent", "tired"]
        )
