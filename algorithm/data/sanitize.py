"""PII and secret scrubbing for exported research data."""

from __future__ import annotations

import re
from typing import Any


PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL_REDACTED]"),
    (re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), "[PHONE_REDACTED]"),
    (re.compile(r"\b(?:sk|api|key|token)[-_]?[A-Za-z0-9]{12,}\b", re.I), "[SECRET_REDACTED]"),
]


def sanitize_text(text: str) -> str:
    value = text or ""
    for pattern, replacement in PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_value(item) for key, item in value.items()}
    return value
