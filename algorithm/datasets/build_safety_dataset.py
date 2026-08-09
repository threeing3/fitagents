"""Build source-labelled safety training rows from canonical examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from algorithm.data.validate_dataset import read_jsonl


def build_safety_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        risk = str(row.get("risk_label") or "low")
        guardrail = row.get("guardrail_result") or {}
        guardrail_action = str(guardrail.get("action") or guardrail.get("status") or "").lower()
        guardrail_triggered = guardrail_action in {"warn", "warning", "block", "blocked", "failed"}
        if risk not in {"medium", "high", "critical"} and not guardrail_triggered:
            continue
        result.append(
            {
                "example_id": row.get("example_id"),
                "user_message": row.get("user_message", ""),
                "assistant_response": row.get("assistant_response", ""),
                "risk_label": risk,
                "guardrail_result": guardrail,
                "source": row.get("source", "unknown"),
                "split": row.get("split", "quarantine"),
                "schema_version": row.get("schema_version", "unknown"),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build safety JSONL")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = build_safety_rows(read_jsonl(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in output:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(output)} safety rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
