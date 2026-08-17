"""CLI entrypoint for the standalone intent adapter service."""

from __future__ import annotations

import argparse
import os


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the FitAgent Qwen intent adapter")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--base-model", default="Qwen/Qwen3-4B")
    parser.add_argument("--adapter", required=True)
    args = parser.parse_args()
    os.environ["INTENT_BASE_MODEL"] = args.base_model
    os.environ["INTENT_ADAPTER_PATH"] = args.adapter
    import uvicorn

    uvicorn.run("algorithm.inference.intent_service:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
