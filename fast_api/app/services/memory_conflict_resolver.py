from __future__ import annotations

import uuid
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
        }
        if self.db is None:
            return results
        for correction in corrections:
            if not isinstance(correction, dict):
                continue
            if correction.get("field") != "injuries":
                continue
            if correction.get("action") not in {"remove", "clear"}:
                continue
            value = str(correction.get("value") or "").lower()
            targets = self._injury_targets(value, message)
            results["corrections_applied"].append({**correction, "targets": targets})
            memory_ids = self._supersede_injury_memories(user_id, targets, correction, message)
            risk_ids = self._correct_risk_notes(user_id, targets, correction, message)
            results["superseded_memory_ids"].extend(memory_ids)
            results["corrected_risk_note_ids"].extend(risk_ids)
        return results

    def _injury_targets(self, value: str, message: str) -> list[str]:
        lowered = f"{value} {message}".lower()
        targets = []
        if any(term in lowered for term in ["肩", "shoulder"]):
            targets.extend(["shoulder", "肩", "肩伤", "右肩", "左肩"])
        if value in {"*", "all", "全部"} or any(term in lowered for term in ["无伤", "没有伤病", "no injuries"]):
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
            haystack = f"{note.body_part or ''} {note.risk_type or ''} {note.description or ''}".lower()
            if "*" not in targets and not any(target.lower() in haystack for target in targets):
                continue
            note.status = "corrected"
            note.description = (
                f"{note.description}\n\n[Correction] 用户已纠正该风险信息："
                f"{correction.get('action')} {correction.get('value')}. 原文：{message[:300]}"
            )
            corrected.append(str(note.id))
        return corrected

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
        injury_like = any(term in haystack for term in ["injury", "pain", "risk", "伤", "痛", "疼", "不适"])
        if not injury_like:
            return False
        return "*" in targets or any(target.lower() in haystack for target in targets if target)
