"""Write a single machine-readable experiment evaluation report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_report(path: Path, experiment_id: str, metrics: dict[str, Any], notes: list[str] | None = None) -> None:
    payload = {
        "experiment_id": experiment_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "notes": notes or [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Wrap metrics in a reproducible report")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    args = parser.parse_args()
    write_report(args.output, args.experiment_id, json.loads(args.metrics.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
