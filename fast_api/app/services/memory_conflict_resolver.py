from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.db import models


class MemoryConflictResolver:
    """Apply user corrections to existing canonical memory records.

    MemoryVerifier prevents bad new writes. This resolver handles the other half:
    old active memories that conflict with a new correction are marked
    superseded instead of silently coexisting with the corrected profile.
    """

    PROFILE_FIELDS = (
        "age",
        "sex",
        "height_cm",
        "weight_kg",
        "activity_level",
        "experience_level",
        "workout_frequency",
        "workout_duration",
        "dietary_preferences",
        "allergies",
        "equipment_available",
    )

    def __init__(self, db: Session):
        self.db = db

    def apply_corrections(
        self,
        user_id: uuid.UUID,
        corrections: list[dict[str, Any]],
        message: str,
    ) -> dict[str, Any]:
        results = {
            "superseded_memory_ids": [],
            "corrected_risk_note_ids": [],
            "corrections_applied": [],
            "affected_categories": [],
            "correction_results": [],
        }
        if self.db is None:
            return results
        from fast_api.app.services.plan_writes import lock_plan_owner

        lock_plan_owner(self.db, user_id)
        affected_categories: set[str] = set()
        for index, correction in enumerate(corrections):
            if not isinstance(correction, dict):
                continue
            field = str(correction.get("field") or "")
            action = str(correction.get("action") or "")
            memory_ids: list[str] = []
            risk_ids: list[str] = []
            targets: list[str] = []
            categories: set[str] = set()
            if field == "injuries" and action in {"remove", "clear"}:
                value = str(correction.get("value") or "").lower()
                targets = self._injury_targets(value, message)
                memory_ids = self._supersede_injury_memories(user_id, targets, correction, message)
                risk_ids = self._correct_risk_notes(user_id, targets, correction, message)
                categories.add("risk")
            elif field == "goal" and action == "set":
                targets = [str(correction.get("value") or "")]
                memory_ids = self._supersede_goal_memories(user_id, correction, message)
                categories.add("profile")
            elif field in self.PROFILE_FIELDS and action == "set":
                memory_ids = self._supersede_profile_field_memories(user_id, correction, message)
                categories.add("profile")
            else:
                continue
            applied = {**correction, "targets": targets}
            results["corrections_applied"].append(applied)
            results["superseded_memory_ids"].extend(memory_ids)
            results["corrected_risk_note_ids"].extend(risk_ids)
            affected_categories.update(categories)
            results["correction_results"].append(
                {
                    "index": index,
                    "correction": applied,
                    "superseded_memory_ids": memory_ids,
                    "corrected_risk_note_ids": risk_ids,
                    "affected_categories": sorted(categories),
                }
            )
        results["affected_categories"] = sorted(affected_categories)
        if results["corrections_applied"]:
            from fast_api.app.services.memory_dependencies import invalidate_derived_memories

            results["derived_memory_ids"] = invalidate_derived_memories(
                self.db,
                user_id,
                [
                    {"table": "long_term_memories", "id": value}
                    for value in results["superseded_memory_ids"]
                ]
                + [
                    {"table": "risk_notes", "id": value}
                    for value in results["corrected_risk_note_ids"]
                ],
                "user_correction",
            )
            from fast_api.app.services.decision_dependencies import DecisionDependencyService
            from fast_api.app.services.plan_adjustment_policy import PlanAdjustmentPolicy

            DecisionDependencyService(self.db).invalidate_changed(
                user_id,
                results["superseded_memory_ids"] + results["corrected_risk_note_ids"],
            )
            PlanAdjustmentPolicy(self.db).invalidate_changed(user_id)
            if results["superseded_memory_ids"]:
                from fast_api.app.services.memory_system import MemoryManager

                revoked_ids = [uuid.UUID(value) for value in results["superseded_memory_ids"]]
                root_categories = self.db.scalars(
                    select(models.LongTermMemory.category).where(
                        models.LongTermMemory.user_id == user_id,
                        models.LongTermMemory.id.in_(revoked_ids),
                    )
                ).all()
                manager = MemoryManager(self.db)
                for category in sorted({value for value in root_categories if value}):
                    manager.update_memory_catalog(user_id, category)
        return results

    def apply_profile_changes(
        self,
        user_id: uuid.UUID,
        *,
        old_goal: str | None,
        new_goal: str | None,
        old_injuries: list[str],
        new_injuries: list[str],
        old_fields: dict[str, Any] | None = None,
        new_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Use confirmed canonical field changes, never semantic retrieval targets."""
        corrections: list[dict[str, Any]] = []
        if new_goal is not None and old_goal != new_goal:
            corrections.append({"field": "goal", "action": "set", "value": new_goal})
        for injury in old_injuries:
            if injury not in new_injuries:
                # Empty or wildcard legacy values cannot authorize broad risk removal.
                if not injury.strip() or injury.lower() in {"*", "all", "全部"}:
                    continue
                corrections.append({"field": "injuries", "action": "remove", "value": injury})
        for field in self.PROFILE_FIELDS:
            if old_fields is not None and new_fields is not None:
                if (
                    field in old_fields
                    and field in new_fields
                    and old_fields[field] != new_fields[field]
                ):
                    corrections.append(
                        {"field": field, "action": "set", "value": new_fields[field]}
                    )
        return self.apply_corrections(user_id, corrections, "用户显式更新档案字段")

    def _supersede_profile_field_memories(
        self, user_id: uuid.UUID, correction: dict[str, Any], message: str
    ) -> list[str]:
        field = correction["field"]
        changed = []
        for memory in self.db.scalars(
            select(models.LongTermMemory).where(
                models.LongTermMemory.user_id == user_id,
                models.LongTermMemory.status == "active",
            )
        ):
            metadata = memory.memory_metadata or {}
            scoped_fields = metadata.get("profile_fields")
            matches = metadata.get("field") == field or metadata.get("profile_field") == field
            if isinstance(scoped_fields, dict):
                matches = matches or field in scoped_fields
            if not matches or memory.memory_type == "correction":
                continue
            memory.status = "superseded"
            memory.valid_until = datetime.utcnow()
            memory.memory_metadata = {
                **metadata,
                "superseded_by_correction": {**correction, "evidence": message[:500]},
            }
            changed.append(str(memory.id))
        return changed

    def _injury_targets(self, value: str, message: str) -> list[str]:
        lowered = f"{value} {message}".lower()
        targets = []
        if any(term in lowered for term in ["肩", "shoulder"]):
            targets.extend(["shoulder", "肩", "肩伤", "右肩", "左肩"])
        if value in {"*", "all", "全部"} or any(
            term in lowered for term in ["无伤", "没有伤病", "no injuries"]
        ):
            targets.append("*")
        return sorted(set(targets or [value or "*"]))

    def _supersede_injury_memories(
        self,
        user_id: uuid.UUID,
        targets: list[str],
        correction: dict[str, Any],
        message: str,
    ) -> list[str]:
        memories = self.db.scalars(
            select(models.LongTermMemory).where(
                models.LongTermMemory.user_id == user_id,
                models.LongTermMemory.status == "active",
            )
        ).all()
        superseded: list[str] = []
        for memory in memories:
            if memory.memory_type == "correction":
                continue
            if not self._memory_matches_injury(memory, targets):
                continue
            metadata = dict(memory.memory_metadata or {})
            metadata["superseded_by_correction"] = {
                "field": correction.get("field"),
                "action": correction.get("action"),
                "value": correction.get("value"),
                "evidence": message[:500],
            }
            memory.memory_metadata = metadata
            memory.status = "superseded"
            memory.valid_until = datetime.utcnow()
            superseded.append(str(memory.id))
        return superseded

    def _supersede_goal_memories(
        self,
        user_id: uuid.UUID,
        correction: dict[str, Any],
        message: str,
    ) -> list[str]:
        memories = self.db.scalars(
            select(models.LongTermMemory).where(
                models.LongTermMemory.user_id == user_id,
                models.LongTermMemory.status == "active",
            )
        ).all()
        superseded: list[str] = []
        for memory in memories:
            if memory.memory_type == "correction" or not self._memory_matches_goal(memory):
                continue
            metadata = dict(memory.memory_metadata or {})
            metadata["superseded_by_correction"] = {
                "field": "goal",
                "action": correction.get("action"),
                "value": correction.get("value"),
                "evidence": message[:500],
            }
            memory.memory_metadata = metadata
            memory.status = "superseded"
            memory.valid_until = datetime.utcnow()
            superseded.append(str(memory.id))
        return superseded

    def _correct_risk_notes(
        self,
        user_id: uuid.UUID,
        targets: list[str],
        correction: dict[str, Any],
        message: str,
    ) -> list[str]:
        risk_notes = self.db.scalars(
            select(models.RiskNote).where(
                models.RiskNote.user_id == user_id,
                models.RiskNote.status == "active",
            )
        ).all()
        corrected: list[str] = []
        for note in risk_notes:
            haystack = (
                f"{note.body_part or ''} {note.risk_type or ''} {note.description or ''}".lower()
            )
            if "*" not in targets and not any(target.lower() in haystack for target in targets):
                continue
            note.status = "corrected"
            note.valid_until = datetime.utcnow()
            note.description = (
                f"{note.description}\n\n[Correction] 用户已纠正该风险信息："
                f"{correction.get('action')} {correction.get('value')}. 原文：{message[:300]}"
            )
            corrected.append(str(note.id))
        return corrected

    def link_correction_memory(
        self,
        user_id: uuid.UUID,
        correction_memory_id: uuid.UUID,
        superseded_memory_ids: list[str],
        message: str,
    ) -> list[str]:
        return self.link_memory_revision(
            user_id,
            correction_memory_id,
            superseded_memory_ids,
            link_type="contradicts",
            reason=message,
            link_metadata={"correction_signal": True},
        )

    def supersede_scoped_memories(
        self,
        user_id: uuid.UUID,
        *,
        memory_type: str,
        source: str,
        scope: dict[str, Any],
        reason: str,
    ) -> list[str]:
        """Supersede active memories only when their persisted scope matches exactly."""
        if self.db is None or not scope:
            return []
        memories = self.db.scalars(
            select(models.LongTermMemory).where(
                models.LongTermMemory.user_id == user_id,
                models.LongTermMemory.memory_type == memory_type,
                models.LongTermMemory.source == source,
                models.LongTermMemory.status == "active",
            )
        ).all()
        superseded: list[str] = []
        for memory in memories:
            metadata = dict(memory.memory_metadata or {})
            if any(metadata.get(key) != value for key, value in scope.items()):
                continue
            metadata["superseded_by_scope_update"] = {
                "scope": scope,
                "reason": reason[:500],
            }
            memory.memory_metadata = metadata
            memory.status = "superseded"
            memory.valid_until = datetime.utcnow()
            superseded.append(str(memory.id))
        from fast_api.app.services.memory_dependencies import invalidate_derived_memories

        invalidate_derived_memories(
            self.db,
            user_id,
            [{"table": "long_term_memories", "id": value} for value in superseded],
            reason,
        )
        return superseded

    def link_memory_revision(
        self,
        user_id: uuid.UUID,
        source_memory_id: uuid.UUID,
        superseded_memory_ids: list[str],
        *,
        link_type: str,
        reason: str,
        link_metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        if self.db is None:
            return []
        link_ids: list[str] = []
        for target_id in superseded_memory_ids:
            target_uuid = uuid.UUID(str(target_id))
            existing = self.db.scalar(
                select(models.MemoryLink).where(
                    models.MemoryLink.user_id == user_id,
                    models.MemoryLink.source_memory_id == source_memory_id,
                    models.MemoryLink.target_memory_id == target_uuid,
                )
            )
            if existing is not None:
                link_ids.append(str(existing.id))
                continue
            link = models.MemoryLink(
                user_id=user_id,
                source_memory_id=source_memory_id,
                target_memory_id=target_uuid,
                link_type=link_type,
                reason=reason[:500],
                link_metadata=link_metadata or {},
            )
            self.db.add(link)
            self.db.flush()
            link_ids.append(str(link.id))
        return link_ids

    def _memory_matches_injury(self, memory: models.LongTermMemory, targets: list[str]) -> bool:
        metadata = memory.memory_metadata or {}
        haystack = " ".join(
            [
                str(memory.memory_type or ""),
                str(memory.category or ""),
                str(memory.content or ""),
                str(memory.summary or ""),
                str(metadata.get("category") or ""),
                str(metadata.get("risk_type") or ""),
                " ".join(str(tag) for tag in metadata.get("tags") or []),
            ]
        ).lower()
        injury_like = any(
            term in haystack for term in ["injury", "pain", "risk", "伤", "痛", "疼", "不适"]
        )
        if not injury_like:
            return False
        return "*" in targets or any(target.lower() in haystack for target in targets if target)

    def _memory_matches_goal(self, memory: models.LongTermMemory) -> bool:
        metadata = dict(memory.memory_metadata or {})
        if str(metadata.get("field") or metadata.get("profile_field") or "") == "goal":
            return True
        if metadata.get("goal"):
            return True
        if any(
            isinstance(entity, dict) and entity.get("type") == "goal"
            for entity in (memory.entities or [])
        ):
            return True
        haystack = " ".join(
            [
                str(memory.fact_kind or ""),
                str(memory.category or ""),
                str(memory.content or ""),
                str(memory.summary or ""),
            ]
        ).lower()
        return memory.category == "profile" and any(
            term in haystack for term in ["目标", "goal", "减脂", "增肌"]
        )
