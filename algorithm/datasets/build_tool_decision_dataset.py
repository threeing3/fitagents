"""Build structured tool-decision examples from canonical traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from algorithm.data.validate_dataset import read_jsonl


def build_tool_decision_rows(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    for row in rows:
        trace = row.get("tool_trace") or []
        names = [str(item.get("tool_name") or item.get("name")) for item in trace if isinstance(item, dict)]
        names = [name for name in names if name and name != "None"]
        if not names and not row.get("intent_label"):
            continue
        result.append(
            {
                "example_id": row.get("example_id"),
                "user_message": row.get("user_message", ""),
                "context_summary": row.get("retrieved_context", {}),
                "intent": row.get("intent_label") or row.get("task_type", "general_chat"),
                "risk_level": row.get("risk_label") or "low",
                "selected_tools": sorted(set(names)),
                "tool_sequence": names,
                "plan_valid": bool(names) and not bool((row.get("guardrail_result") or {}).get("error")),
                "source": row.get("source", "unknown"),
                "split": row.get("split", "quarantine"),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build tool-decision JSONL")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = build_tool_decision_rows(read_jsonl(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in output:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(output)} tool-decision rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
