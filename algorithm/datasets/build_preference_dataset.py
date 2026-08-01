"""Build conservative preference pairs from feedback and outcome fields."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from algorithm.data.schemas import PreferencePair
from algorithm.data.validate_dataset import read_jsonl


def build_preference_pairs(rows: list[dict]) -> list[dict]:
    pairs: list[dict] = []
    # Pair only explicit chosen/rejected fields.  Never infer preference from
    # an unlabeled response merely because it is longer or newer.
    for row in rows:
        chosen = row.get("chosen")
        rejected = row.get("rejected")
        if not chosen or not rejected:
            continue
        pair = PreferencePair(
            example_id=str(row.get("example_id") or ""),
            prompt=str(row.get("prompt") or row.get("user_message") or ""),
            chosen=str(chosen),
            rejected=str(rejected),
            preference_reason=list(row.get("preference_reason") or []),
            feedback_source=str(row.get("feedback_source") or row.get("source") or "unknown"),
            guardrail_comparison=row.get("guardrail_comparison") or {},
            business_outcome_comparison=row.get("business_outcome_comparison") or {},
            source=str(row.get("source") or "agent_trace"),
            split=str(row.get("split") or "quarantine"),
        )
        if not pair.validate():
            pairs.append(pair.to_dict())
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Build preference-pair JSONL")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    pairs = build_preference_pairs(read_jsonl(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in pairs:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(pairs)} preference pairs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
