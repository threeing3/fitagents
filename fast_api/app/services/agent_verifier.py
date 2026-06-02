from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VerificationIssue:
    issue_id: str
    severity: str
    message: str
    repairable: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "severity": self.severity,
            "message": self.message,
            "repairable": self.repairable,
            "evidence": self.evidence,
        }


@dataclass
class VerificationResult:
    passed: bool
    issues: list[VerificationIssue] = field(default_factory=list)
    repair_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
            "repair_actions": self.repair_actions,
        }


class AgentVerifier:
    """Rule-first verifier for agent outputs.

    This is intentionally lightweight. It gives the project a Claude Code-like
    "execute -> verify -> repair" loop without requiring a framework migration.
    """

    PLAN_TERMS = [
        "training plan",
        "workout plan",
        "today's workout",
        "sets",
        "reps",
        "训练计划",
        "今日训练",
        "今天练",
        "组",
        "次数",
    ]

    def verify_plan(
        self,
        plan_payload: dict[str, Any] | None,
        profile_payload: dict[str, Any] | None = None,
        context_packet: dict[str, Any] | None = None,
    ) -> VerificationResult:
        issues: list[VerificationIssue] = []
        profile_payload = profile_payload or {}
        context_packet = context_packet or {}
        plan_payload = plan_payload or {}
        plan_json = plan_payload.get("plan") if "plan" in plan_payload else plan_payload
        if not isinstance(plan_json, dict):
            issues.append(
                VerificationIssue(
                    "plan_not_object",
                    "error",
                    "训练计划不是结构化对象，无法可靠执行。",
                    repairable=False,
                )
            )
            return VerificationResult(False, issues)

        training_days = plan_json.get("training_days")
        if not isinstance(training_days, list) or not training_days:
            issues.append(
                VerificationIssue(
                    "missing_training_days",
                    "error",
                    "训练计划缺少 training_days，用户无法按天执行。",
                    repairable=False,
                )
            )
        else:
            target_frequency = profile_payload.get("workout_frequency")
            if isinstance(target_frequency, int):
                lower = max(1, target_frequency - 1)
                upper = min(7, target_frequency + 1)
                if not lower <= len(training_days) <= upper:
                    issues.append(
                        VerificationIssue(
                            "frequency_mismatch",
                            "warn",
                            "训练天数和用户档案中的每周训练频率不够匹配。",
                            evidence={
                                "target_frequency": target_frequency,
                                "actual_days": len(training_days),
                            },
                        )
                    )

            for day in training_days:
                exercises = day.get("exercises") if isinstance(day, dict) else None
                if not isinstance(exercises, list) or not exercises:
                    issues.append(
                        VerificationIssue(
                            "day_missing_exercises",
                            "error",
                            "某个训练日缺少动作列表。",
                            evidence={"day": day.get("day") if isinstance(day, dict) else None},
                            repairable=False,
                        )
                    )
                    continue
                for exercise in exercises:
                    if not isinstance(exercise, dict):
                        continue
                    notes = str(exercise.get("notes") or "").lower()
                    if "pain" not in notes and "疼" not in notes and "痛" not in notes:
                        issues.append(
                            VerificationIssue(
                                "missing_pain_stop_note",
                                "warn",
                                "动作缺少疼痛/不适时停止的安全提示。",
                                evidence={
                                    "day": day.get("day"),
                                    "exercise": exercise.get("name"),
                                },
                            )
                        )
                        break

        nutrition = plan_json.get("nutrition")
        if not isinstance(nutrition, dict):
            issues.append(
                VerificationIssue(
                    "missing_nutrition_block",
                    "warn",
                    "计划缺少营养目标，减脂/增肌闭环不完整。",
                )
            )
        elif profile_payload.get("target_calories") and not nutrition.get("target_calories"):
            issues.append(
                VerificationIssue(
                    "missing_target_calories",
                    "warn",
                    "计划营养部分缺少目标热量。",
                )
            )

        if not plan_json.get("review_cadence"):
            issues.append(
                VerificationIssue(
                    "missing_review_cadence",
                    "warn",
                    "计划缺少复盘周期，长期陪跑闭环不完整。",
                )
            )

        medical_context = self._has_medical_context(context_packet)
        if medical_context and not self._plan_has_medical_boundary(plan_json):
            issues.append(
                VerificationIssue(
                    "missing_medical_boundary_note",
                    "warn",
                    "用户存在健康/用药背景，计划缺少训练安全边界提醒。",
                )
            )

        return VerificationResult(
            passed=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            repair_actions=[issue.issue_id for issue in issues if issue.repairable],
        )

    def repair_plan(
        self,
        plan_payload: dict[str, Any],
        verification: dict[str, Any],
        profile_payload: dict[str, Any] | None = None,
        context_packet: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repaired = deepcopy(plan_payload or {})
        plan_json = repaired.get("plan") if "plan" in repaired else repaired
        if not isinstance(plan_json, dict):
            return repaired

        issue_ids = {issue.get("issue_id") for issue in verification.get("issues", []) if isinstance(issue, dict)}
        if "missing_pain_stop_note" in issue_ids:
            for day in plan_json.get("training_days") or []:
                for exercise in day.get("exercises") or []:
                    if not isinstance(exercise, dict):
                        continue
                    notes = str(exercise.get("notes") or "").strip()
                    safety = "Stop or regress the movement if pain, dizziness, chest tightness, or unusual discomfort appears."
                    exercise["notes"] = f"{notes} {safety}".strip()

        if "missing_review_cadence" in issue_ids:
            plan_json["review_cadence"] = "weekly"

        if "missing_nutrition_block" in issue_ids or "missing_target_calories" in issue_ids:
            profile_payload = profile_payload or {}
            plan_json["nutrition"] = {
                "target_calories": profile_payload.get("target_calories"),
                "protein_g": profile_payload.get("target_protein_g"),
                "carbs_g": profile_payload.get("target_carbs_g"),
                "fat_g": profile_payload.get("target_fat_g"),
                "principles": [
                    "Anchor each meal around protein.",
                    "Keep the calorie target flexible and adjust weekly from real adherence.",
                ],
            }

        if "missing_medical_boundary_note" in issue_ids:
            plan_json["safety_boundary"] = (
                "This plan is fitness guidance, not medical diagnosis. With medical history or medication, "
                "keep intensity conservative and follow clinician guidance for training limits."
            )

        return repaired

    def verify_response(
        self,
        response: str,
        user_message: str,
        context_packet: dict[str, Any] | None = None,
    ) -> VerificationResult:
        context_packet = context_packet or {}
        policy = context_packet.get("current_request_policy") or {}
        issues: list[VerificationIssue] = []
        response = response or ""
        user_message = user_message or ""

        if len(response.strip()) < 20:
            issues.append(
                VerificationIssue(
                    "response_too_short",
                    "error",
                    "回复过短，无法满足用户的实际问题。",
                    repairable=True,
                )
            )

        allow_plan_content = bool(policy.get("allow_plan_content"))
        should_generate_plan = bool(policy.get("should_generate_plan"))
        if not allow_plan_content and not should_generate_plan and self._contains_plan_content(response):
            issues.append(
                VerificationIssue(
                    "old_plan_carryover",
                    "error",
                    "当前消息没有要求计划，但回复中出现训练计划内容，疑似旧命令粘连。",
                    repairable=True,
                )
            )

        if self._has_medical_context(context_packet) and self._response_mentions_training_intensity(response):
            if not self._has_medical_boundary(response):
                issues.append(
                    VerificationIssue(
                        "missing_medical_boundary_in_response",
                        "warn",
                        "用户存在健康/用药背景，回复缺少医疗边界提示。",
                        repairable=True,
                    )
                )

        return VerificationResult(
            passed=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            repair_actions=[issue.issue_id for issue in issues if issue.repairable],
        )

    def repair_response(
        self,
        verification: dict[str, Any],
        context_packet: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        issue_ids = {issue.get("issue_id") for issue in verification.get("issues", []) if isinstance(issue, dict)}
        additions: list[str] = []
        if "response_too_short" in issue_ids:
            additions.append("我会基于你当前这条消息重新聚焦回答，避免只给一句空泛建议。")
        if "old_plan_carryover" in issue_ids:
            additions.append("本轮校验已阻止旧计划指令继续生效：下面只回答你当前这条消息，不自动追加训练计划。")
        if "missing_medical_boundary_in_response" in issue_ids:
            additions.append("安全边界：涉及疾病、用药、胸闷、头晕、异常心率或明确疼痛时，我只能做训练强度和动作选择上的保守建议，不能替代医生诊断或用药建议。")
        repair_text = ""
        if additions:
            repair_text = "\n\n---\nAgent 自检补充：\n" + "\n".join(f"- {item}" for item in additions)
        return {
            "repair_text": repair_text,
            "repair_actions": sorted(issue_ids),
            "repaired": bool(repair_text),
        }

    def _contains_plan_content(self, text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in self.PLAN_TERMS)

    def _has_medical_context(self, context_packet: dict[str, Any]) -> bool:
        memories = context_packet.get("relevant_memories") or []
        risks = context_packet.get("active_risk_notes") or []
        if risks:
            return True
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            memory_type = str(memory.get("memory_type") or "")
            category = str(memory.get("category") or "")
            content = f"{memory.get('summary') or ''} {memory.get('content') or ''}".lower()
            if memory_type == "medical_context" or category == "risk":
                return True
            if any(term in content for term in ["thyroid", "medication", "甲亢", "甲状腺", "用药", "赛治"]):
                return True
        return False

    def _plan_has_medical_boundary(self, plan_json: dict[str, Any]) -> bool:
        text = str(plan_json).lower()
        return any(term in text for term in ["medical", "doctor", "clinician", "医生", "就医", "用药"])

    def _response_mentions_training_intensity(self, response: str) -> bool:
        lowered = response.lower()
        return any(term in lowered for term in ["rpe", "intensity", "heart rate", "hiit", "训练强度", "心率", "高强度"])

    def _has_medical_boundary(self, response: str) -> bool:
        lowered = response.lower()
        return any(term in lowered for term in ["doctor", "clinician", "医生", "医师", "就医", "用药建议", "不能替代"])
