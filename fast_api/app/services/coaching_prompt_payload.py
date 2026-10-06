"""Lossless memory-view references for serialized coaching prompts only."""

import json
from typing import Any

REFERENCE_POLICY = (
    "Resolve _memory_ref by id in relevant_memories. Without _memory_fields, use the complete "
    "source record. With _memory_fields, copy only those fields. Other fields on the view "
    "override or extend the source. References do not omit evidence or weaken constraints."
)


def serialize_coaching_prompt(payload: dict[str, Any]) -> str:
    """Reference repeated fields only if the complete resulting prompt is smaller."""

    def encode(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    baseline = encode(payload)
    packet = payload.get("context_packet")
    if not isinstance(packet, dict) or "_memory_reference_policy" in packet:
        return baseline
    records = packet.get("relevant_memories") or []
    if not isinstance(records, list):
        return baseline
    sources = {}
    for record in records:
        if not isinstance(record, dict):
            return baseline
        key = record.get("id")
        if (
            not isinstance(key, str)
            or not key
            or key in sources
            or "_memory_ref" in record
            or "_memory_fields" in record
        ):
            return baseline
        sources[key] = record

    def project(view: Any) -> Any:
        if not isinstance(view, dict) or "_memory_ref" in view or "_memory_fields" in view:
            return view
        key = view.get("id")
        if not isinstance(key, str):
            return view
        source = sources.get(key)
        if source is None:
            return view
        if view == source:
            return {"_memory_ref": view["id"]}
        shared = [key for key in view if key in source and view[key] == source[key]]
        if not shared:
            return view
        return {
            "_memory_ref": view["id"],
            "_memory_fields": shared,
            **{key: value for key, value in view.items() if key not in shared},
        }

    projected = dict(packet)
    for key in (
        "world_memories",
        "experience_memories",
        "observation_memories",
        "opinion_memories",
    ):
        if isinstance(packet.get(key), list):
            projected[key] = [project(item) for item in packet[key]]
    guidance = packet.get("strategy_memory_guidance")
    if isinstance(guidance, dict):
        projected_guidance = dict(guidance)
        for key in ("successful_strategies", "failed_strategies"):
            if isinstance(guidance.get(key), list):
                projected_guidance[key] = [project(item) for item in guidance[key]]
        projected["strategy_memory_guidance"] = projected_guidance
    projected["_memory_reference_policy"] = REFERENCE_POLICY
    candidate = encode({**payload, "context_packet": projected})
    return candidate if len(candidate.encode("utf-8")) < len(baseline.encode("utf-8")) else baseline
