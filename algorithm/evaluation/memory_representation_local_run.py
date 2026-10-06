"""Bounded memory-use comparison through an explicitly local model server."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from algorithm.evaluation.memory_representation_eval import prepare, score

ENDPOINT = "http://127.0.0.1:8079/v1/chat/completions"


def request_body(row):
    return {
        "model": "Qwen3-4B-Q4_K_M.gguf",
        "messages": [
            {"role": "system", "content": row["system"]},
            {"role": "user", "content": row["user"]},
        ],
        "temperature": 0,
        "seed": 42,
        "max_tokens": 256,
        "stream": False,
    }


def ordered_inputs(*, schema_example=False):
    rows = prepare(schema_example=schema_example)
    ordered = []
    for i in range(0, len(rows), 2):
        pair = rows[i : i + 2]
        ordered.extend(pair if (i // 2) % 2 == 0 else reversed(pair))
    return ordered


def run(output: Path, *, schema_example=False):
    output.mkdir(parents=True, exist_ok=False)
    rows = ordered_inputs(schema_example=schema_example)
    # These files contain only synthetic evaluation material, never credentials.
    (output / "inputs.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    transport = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    predictions = []
    with (output / "run.log").open("w", encoding="utf-8") as log_file:

        def log(message):
            line = f"{datetime.now(timezone.utc).isoformat()} {message}"
            print(line, flush=True)
            log_file.write(line + "\n")
            log_file.flush()

        log("START local Qwen3-4B Q4_K_M; 16 calls; temp=0 seed=42 max_tokens=256; no retry")
        with (output / "predictions.jsonl").open("w", encoding="utf-8") as raw_file:
            for row in rows:
                started = time.perf_counter()
                prediction = {
                    "case_id": row["case_id"],
                    "variant": row["variant"],
                    "revision": row["revision"],
                    "response": "",
                    "input_bytes": row["input_bytes"],
                }
                try:
                    request = urllib.request.Request(
                        ENDPOINT,
                        data=json.dumps(request_body(row), ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    with transport.open(request, timeout=60) as response:
                        raw = json.loads(response.read().decode("utf-8"))
                    prediction["raw_response"] = raw
                    prediction["response"] = raw["choices"][0]["message"]["content"]
                    prediction["finish_reason"] = raw["choices"][0]["finish_reason"]
                    prediction["usage"] = raw.get("usage")
                except Exception as exc:
                    prediction["error_type"] = type(exc).__name__
                    log(
                        f"STOP transport/response failure {row['case_id']}/{row['variant']}: {type(exc).__name__}"
                    )
                prediction["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                predictions.append(prediction)
                raw_file.write(json.dumps(prediction, ensure_ascii=False) + "\n")
                raw_file.flush()
                log(
                    f"CALL {len(predictions)}/16 {row['case_id']}/{row['variant']} {prediction['latency_ms']}ms"
                )
                if "error_type" in prediction:
                    log("INCOMPLETE no aggregate score; preserve completed calls")
                    return None
        report = score(predictions, revision=rows[0]["revision"])
        report["model"] = "local Qwen3-4B Q4_K_M, reasoning off"
        report["settings"] = {"temperature": 0, "seed": 42, "max_tokens": 256}
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log("COMPLETE " + json.dumps(report["summary"], ensure_ascii=False))
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema-example", action="store_true")
    args = parser.parse_args()
    run(args.output, schema_example=args.schema_example)
