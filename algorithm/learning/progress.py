"""Persistent progress state for the project-based learning mode."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .curriculum import CURRICULUM, get_module


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "learner": "",
        "modules": {
            card.module_id: {
                "status": "not_started",
                "evidence": "",
                "note": "",
                "updated_at": None,
            }
            for card in CURRICULUM
        },
    }


class ProgressStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return default_state()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("modules"), dict):
            raise ValueError(f"invalid learning progress file: {self.path}")
        state = default_state()
        state.update({key: value for key, value in payload.items() if key != "modules"})
        for module_id in state["modules"]:
            state["modules"][module_id].update(payload["modules"].get(module_id, {}))
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def update(self, module_id: str, status: str, evidence: str = "", note: str = "") -> dict[str, Any]:
        get_module(module_id)
        if status not in {"not_started", "in_progress", "mastered"}:
            raise ValueError(f"invalid status: {status}")
        state = self.load()
        state["modules"][module_id].update(
            {
                "status": status,
                "evidence": evidence,
                "note": note,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.save(state)
        return state

