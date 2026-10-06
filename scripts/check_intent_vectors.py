"""Prepare a pinned local model and run synthetic retrieval diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fast_api.app.services.intent_cascade import ExampleVectorIndex  # noqa: E402
from fast_api.app.services.intent_embeddings import MODEL_DIR  # noqa: E402

REVISION = "75c43b069aac4d136ba6bc1122f995fedcfd2781"
CASES = [
    ("我想翻翻之前锻炼的历史", "memory_query"),
    ("运动以后晚饭该吃些什么", "nutrition_advice"),
    ("帮我把今日五公里慢跑保存下来", "training_log"),
    ("睡眠不足，现在适不适合继续练", "recovery_check"),
    ("渐进超负荷是什么意思", "concept_explanation"),
    ("不要安排训练，帮我看看之前的记录", "memory_query"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    log_dir = ROOT / "logs/experiments"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"intent_vectors_{datetime.now():%Y%m%d_%H%M%S_%f}.jsonl"
    with log_path.open("x", encoding="utf-8") as log:

        def record(item):
            log.write(json.dumps(item, ensure_ascii=False) + "\n")
            log.flush()
            print(json.dumps(item, ensure_ascii=True), flush=True)

        record({"event": "start", "revision": REVISION, "data": "synthetic_only"})
        try:
            if args.download:
                from huggingface_hub import snapshot_download

                record({"event": "download_start", "repo": "Xenova/bge-small-zh-v1.5"})
                snapshot_download(
                    "Xenova/bge-small-zh-v1.5",
                    revision=REVISION,
                    allow_patterns=["tokenizer.json", "onnx/model.onnx"],
                    local_dir=MODEL_DIR,
                    max_workers=2,
                )
                record({"event": "download_complete"})
            index = ExampleVectorIndex()
            available = True
            for message, expected in CASES:
                started = time.perf_counter()
                candidates, status = index.retrieve(message)
                available = available and status == "available"
                record(
                    {
                        "event": "case",
                        "message": message,
                        "expected": expected,
                        "status": status,
                        "candidates": candidates,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "expected_in_top3": any(c["intent"] == expected for c in candidates),
                        "exit_authorized": False,
                    }
                )
            record({"event": "end", "available": available, "log": str(log_path)})
            return 0 if available else 1
        except Exception as exc:
            record({"event": "failed", "error_type": type(exc).__name__})
            raise


if __name__ == "__main__":
    raise SystemExit(main())
