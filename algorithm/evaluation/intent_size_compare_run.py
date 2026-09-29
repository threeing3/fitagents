"""Run frozen intent cases against one local OpenAI-compatible model server."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any


def request_payload(prompt: str, message: str, model_id: str) -> dict[str, Any]:
    """Use one deterministic non-thinking protocol for every model size."""

    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ],
        "temperature": 0,
        "top_p": 1,
        "seed": 42,
        "max_tokens": 256,
        "stream": False,
    }


def infer_one(endpoint: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        result = json.loads(response.read().decode("utf-8"))
    text = result["choices"][0]["message"]["content"]
    if not isinstance(text, str):
        raise ValueError("model response has no text content")
    return {"text": text, "usage": result.get("usage")}


def run_cases(
    rows: list[dict[str, Any]],
    *,
    prompt: str,
    prompt_id: str,
    model_id: str,
    endpoint: str,
    output: Path,
    timeout_seconds: int,
) -> None:
    """Write each completed case immediately; never overwrite a previous attempt."""

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        for row in rows:
            case_id = str(row["case_id"])
            payload = request_payload(prompt, str(row["user_message"]), model_id)
            started = time.perf_counter()
            prediction = infer_one(endpoint, payload, timeout_seconds)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            record = {
                "case_id": case_id,
                "model_id": model_id,
                "prompt_id": prompt_id,
                "text": prediction["text"],
                "latency_ms": elapsed_ms,
                "usage": prediction["usage"],
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"{case_id}\t{elapsed_ms} ms", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a frozen intent model-size comparison")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--prompt-id", default="intent-size-compare-v1")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/chat/completions")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.endpoint != "http://127.0.0.1:8080/v1/chat/completions":
        parser.error("only the local model server endpoint is allowed")

    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.limit is not None:
        rows = rows[: args.limit]
    run_cases(
        rows,
        prompt=args.prompt_file.read_text(encoding="utf-8"),
        prompt_id=args.prompt_id,
        model_id=args.model_id,
        endpoint=args.endpoint,
        output=args.output,
        timeout_seconds=args.timeout_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
