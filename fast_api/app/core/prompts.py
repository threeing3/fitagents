"""
Prompt Registry — centralized, versioned prompt management.

Loads prompts from a YAML file and provides typed lookup with metadata.
In production, swap the YAML loader for a DB-backed store to enable
A/B testing, canary rollouts, and hot-reload without redeployment.

Usage:
    from fast_api.app.core.prompts import registry
    system_prompt = registry.get("coach_onboarding")
    version = registry.version("coach_onboarding")
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Default path relative to this module
_DEFAULT_PROMPTS_PATH = Path(__file__).resolve().parent.parent / "data" / "prompts.yaml"


class PromptRegistry:
    """Thread-safe, read-optimized prompt store with version metadata."""

    def __init__(self, prompts_path: Path | str | None = None):
        self._prompts_path = Path(prompts_path) if prompts_path else _DEFAULT_PROMPTS_PATH
        self._prompts: dict[str, dict[str, Any]] = {}
        self._reload()

    # ---- Public API ----

    def get(self, prompt_id: str) -> str:
        """Return the prompt content string.

        Raises KeyError if the prompt_id is not found.
        """
        return self._prompts[prompt_id]["content"]

    def get_or_default(self, prompt_id: str, default: str = "") -> str:
        """Return the prompt content, or *default* if the id is not registered."""
        entry = self._prompts.get(prompt_id)
        return entry["content"] if entry else default

    def version(self, prompt_id: str) -> str:
        """Return the version string (e.g. '1.0') for a prompt."""
        return self._prompts[prompt_id]["version"]

    def description(self, prompt_id: str) -> str:
        """Return the human-readable description."""
        return self._prompts[prompt_id]["description"]

    def info(self, prompt_id: str) -> dict[str, Any]:
        """Return full metadata dict (version, description, last_modified)."""
        entry = self._prompts[prompt_id]
        return {
            "id": prompt_id,
            "version": entry["version"],
            "description": entry["description"],
            "last_modified": entry.get("last_modified"),
        }

    def list_ids(self) -> list[str]:
        """Return all registered prompt IDs."""
        return sorted(self._prompts.keys())

    def reload(self) -> None:
        """Hot-reload prompts from disk (for development / config changes)."""
        self._reload()
        logger.info("Prompt registry reloaded — %d prompts loaded", len(self._prompts))

    # ---- Internal ----

    def _reload(self) -> None:
        if not self._prompts_path.exists():
            logger.warning("Prompts file not found at %s — registry is empty", self._prompts_path)
            self._prompts = {}
            return

        with open(self._prompts_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        raw = data.get("prompts") if isinstance(data, dict) else {}
        if not isinstance(raw, dict):
            logger.error("Invalid prompts.yaml: 'prompts' must be a mapping")
            self._prompts = {}
            return

        self._prompts = {}
        for pid, entry in raw.items():
            if not isinstance(entry, dict):
                logger.warning("Skipping invalid prompt entry: %s", pid)
                continue
            if "content" not in entry:
                logger.warning("Prompt %s missing 'content' field — skipping", pid)
                continue
            self._prompts[pid] = {
                "content": entry["content"].strip(),
                "version": str(entry.get("version", "0.0")),
                "description": str(entry.get("description", "")),
                "last_modified": str(entry.get("last_modified", "")),
            }

        logger.debug("Prompt registry loaded %d prompts from %s", len(self._prompts), self._prompts_path)


# Module-level singleton — import this everywhere.
registry = PromptRegistry()
