"""Merge an explicitly dated session without reinterpreting legacy ordinal days."""

from copy import deepcopy
from datetime import date
from typing import Any


def merge_dated_session(
    existing: dict[str, Any], single: dict[str, Any], *, replace_date: str | None = None
) -> dict[str, Any]:
    days = existing.get("training_days")
    if not isinstance(days, list) or not days:
        raise ValueError("Existing plan has no dated sessions; clarify the date mapping first")
    seen = set()
    for day in days:
        if not isinstance(day, dict) or not day.get("date"):
            raise ValueError("Existing plan has undated sessions; clarify the date mapping first")
        try:
            day_date = date.fromisoformat(day["date"]).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError("Existing plan has an invalid session date") from exc
        if day_date in seen:
            raise ValueError("Existing plan has duplicate session dates; clarify first")
        seen.add(day_date)
    constraints = single["request_constraints"]
    target = constraints["target_date"]
    replacement = deepcopy(single["training_days"][0])
    if replacement.get("date") != target:
        raise ValueError("requested_date_mismatch")
    candidate = deepcopy(existing)
    if replace_date is not None and replace_date != target:
        if replace_date not in seen:
            raise ValueError("Source session does not exist; clarify before moving")
        if target in seen:
            raise ValueError("Target date already has a session; clarify before moving")
        candidate["training_days"] = [
            day for day in candidate["training_days"] if day["date"] != replace_date
        ]
    for index, day in enumerate(candidate["training_days"]):
        if day["date"] == target:
            replacement["day"] = day.get("day", index + 1)
            candidate["training_days"][index] = {**day, **replacement}
            break
    else:
        replacement["day"] = max((int(day.get("day", 0)) for day in days), default=0) + 1
        candidate["training_days"].append(replacement)
    locked = dict(candidate.get("dated_constraints") or {})
    previous = candidate.get("request_constraints") or {}
    if previous.get("target_date") and previous.get("exercise_type"):
        locked[previous["target_date"]] = previous["exercise_type"]
    if replace_date is not None and replace_date != target:
        locked.pop(replace_date, None)
    locked[target] = constraints["exercise_type"]
    candidate["dated_constraints"] = locked
    candidate["request_constraints"] = deepcopy(constraints)
    return candidate
