"""Deterministic text and pair deduplication."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def content_key(*parts: str) -> str:
    normalized = "\n".join(normalize_text(part) for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def deduplicate_records(records: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    duplicates = 0
    for record in records:
        key = content_key(*(str(record.get(field, "")) for field in fields))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(record)
    return unique, duplicates
