"""Export safe TrainingExample rows from local SQLite data and agent logs.

The exporter is deliberately conservative: it only reads local files, hashes
identifiers, scrubs common PII patterns, and records provenance.  Production
exports should provide a deployment-specific salt instead of the local default.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .deduplicate import deduplicate_records
from .sanitize import sanitize_value
from .schemas import DatasetManifest, TrainingExample, stable_hash
from .split_dataset import split_records


REQUEST_RE = re.compile(r"请求 ID：\s*([^\s]+)")
USER_RE = re.compile(r"用户 ID：\s*([^\s]+)")


def _sqlite_rows(db_path: Path, salt: str) -> list[TrainingExample]:
    if not db_path.exists():
        return []
    rows: list[TrainingExample] = []
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        query = """
        SELECT cm.id, cm.user_id, cm.session_id, cm.role, cm.content,
               cm.message_metadata
        FROM chat_messages cm
        ORDER BY cm.created_at, cm.id
        """
        last_user_message_by_session: dict[str, str] = {}
        for row in connection.execute(query):
            session_key = str(row["session_id"])
            if row["role"] == "user":
                # Keep the latest user turn so assistant rows become usable
                # supervised examples even when metadata omits the prompt.
                last_user_message_by_session[session_key] = str(row["content"] or "")
                continue
            if row["role"] != "assistant":
                continue
            metadata: dict[str, Any] = {}
            try:
                metadata = json.loads(row["message_metadata"] or "{}")
            except json.JSONDecodeError:
                metadata = {"raw_metadata": row["message_metadata"]}
            user_hash = stable_hash(str(row["user_id"]), salt)
            session_hash = stable_hash(str(row["session_id"]), salt)
            payload = sanitize_value(metadata)
            rows.append(
                TrainingExample(
                    example_id=f"chat-{row['id']}",
                    task_type=str(payload.get("intent") or "coach_response"),
                    user_message=str(
                        payload.get("user_message")
                        or last_user_message_by_session.get(session_key)
                        or "context unavailable"
                    ),
                    user_hash=user_hash,
                    session_hash=session_hash,
                    retrieved_context=payload.get("context") or {},
                    tool_trace=payload.get("tool_calls") or [],
                    assistant_response=str(row["content"] or ""),
                    intent_label=payload.get("intent"),
                    risk_label=payload.get("risk_level"),
                    guardrail_result=payload.get("guardrail") or {},
                    model_version=str(payload.get("model") or (row["tokenizer_model"] if "tokenizer_model" in row.keys() else None) or "unknown"),
                    prompt_version=str(payload.get("prompt_version") or "unknown"),
                    rule_version=str(payload.get("rule_version") or "unknown"),
                    source="agent_trace",
                )
            )
    finally:
        connection.close()
    return rows


def _log_rows(log_dir: Path, salt: str) -> list[TrainingExample]:
    rows: list[TrainingExample] = []
    if not log_dir.exists():
        return rows
    for path in sorted(log_dir.glob("*.log")):
        text = path.read_text(encoding="utf-8", errors="replace")
        request_match = REQUEST_RE.search(text)
        user_match = USER_RE.search(text)
        request_id = request_match.group(1) if request_match else path.stem
        user_id = user_match.group(1) if user_match else "unknown"
        tool_trace: list[dict[str, Any]] = []
        for line in text.splitlines():
            if "输出摘要：" not in line:
                continue
            raw = line.split("输出摘要：", 1)[1].strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("tool_name"):
                tool_trace.append(payload)
        final_response = ""
        final_marker = "最终回复摘要："
        if final_marker in text:
            final_response = text.split(final_marker, 1)[1].split("\n\n", 1)[0].strip()
        intent_label = None
        for payload in tool_trace:
            if payload.get("tool_name") == "context.build":
                output = payload.get("output_json") or payload.get("output_summary") or {}
                if isinstance(output, dict):
                    intent_label = output.get("intent") or output.get("current_intent")
                    break
        user_message = ""
        for marker in ("当前用户消息：", "用户消息：", "user_message:"):
            if marker in text:
                user_message = text.split(marker, 1)[1].splitlines()[0].strip()
                break
        rows.append(
            TrainingExample(
                example_id=f"log-{path.stem}",
                task_type="agent_run",
                user_message=sanitize_value(user_message or "log-only example"),
                user_hash=stable_hash(user_id, salt),
                session_hash=stable_hash(request_id, salt),
                tool_trace=tool_trace or [{"log_path": path.name, "log_chars": len(text)}],
                assistant_response=sanitize_value(final_response),
                intent_label=intent_label,
                source="agent_trace",
                split="quarantine",
            )
        )
    return rows


def export_training_examples(
    output_path: Path,
    db_paths: list[Path] | None = None,
    log_dir: Path | None = None,
    salt: str = "local-dev-only",
) -> DatasetManifest:
    db_paths = db_paths or []
    records = [row.to_dict() for path in db_paths for row in _sqlite_rows(path, salt)]
    records.extend(row.to_dict() for row in _log_rows(log_dir, salt) if log_dir)
    records, duplicate_count = deduplicate_records(records, ("user_hash", "user_message", "assistant_response"))
    records, split_counts = split_records(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(sanitize_value(record), ensure_ascii=False) + "\n")
    manifest = DatasetManifest(
        dataset_name="fitness_training_examples",
        dataset_version="v1",
        source_files=[str(path) for path in [*db_paths, log_dir] if path],
        row_counts={"training_examples": len(records)},
        split_counts=split_counts,
        source_counts={"agent_trace": len(records)},
        user_count=len({row.get("user_hash") for row in records}),
        scenario_count=len({row.get("task_type") for row in records}),
        notes=[f"deduplicated={duplicate_count}", "local export; verify source authorization before training"],
    )
    manifest.write_json(output_path.with_suffix(".manifest.json"))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export governed training examples")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path, action="append", default=[])
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--salt", default="local-dev-only")
    args = parser.parse_args()
    manifest = export_training_examples(args.output, args.db, args.log_dir, args.salt)
    print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
