"""Build chat-style SFT JSONL from canonical TrainingExample rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from algorithm.data.validate_dataset import read_jsonl


def build_sft_rows(
    rows: list[dict[str, Any]], include_splits: set[str] | None = None
) -> list[dict[str, Any]]:
    include_splits = include_splits or {"train"}
    result: list[dict[str, Any]] = []
    for row in rows:
        if (
            row.get("split") not in include_splits
            or row.get("training_eligible") is not True
            or not row.get("assistant_response")
        ):
            continue
        context = row.get("retrieved_context") or {}
        context_text = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
        is_intent_task = row.get("task_type") == "intent_decision_v2"
        system_content = (
            "你是 FitAgent 的意图决策器。只输出符合 IntentDecisionV2 的 JSON，不输出思考过程。"
            if is_intent_task
            else "你是安全、具体、个性化的健身教练。遵守结构化规则和安全边界。"
        )
        result.append(
            {
                "example_id": row.get("example_id"),
                "task_type": row.get("task_type", "coach_response"),
                "messages": [
                    {
                        "role": "system",
                        "content": system_content,
                    },
                    {
                        "role": "user",
                        "content": f"用户问题：{row.get('user_message', '')}\n上下文：{context_text}",
                    },
                    {"role": "assistant", "content": row.get("assistant_response", "")},
                ],
                "source": row.get("source", "unknown"),
                "label_source": row.get("label_source", "unknown"),
                "template_family": row.get("template_family"),
                "split": row.get("split"),
                "schema_version": row.get("schema_version", "unknown"),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SFT JSONL")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--include-split",
        action="append",
        choices=["train", "validation", "test"],
        help="split to include; repeat for multiple splits (default: train)",
    )
    args = parser.parse_args()
    rows = build_sft_rows(
        read_jsonl(args.input), include_splits=set(args.include_split or ["train"])
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} SFT rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
