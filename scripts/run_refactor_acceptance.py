"""Run isolated synthetic business journeys without a unit-test framework."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from algorithm.evaluation.fitagent_journey_eval import evaluate_journeys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New result directory")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    log_path = args.output / "steps.log"
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"{datetime.now(timezone.utc).isoformat()} START synthetic journeys\n")
        log.write("Isolated synthetic users, SQLite, scripted feedback; no live model.\n")
        log.flush()
        try:
            report = evaluate_journeys()
        except Exception as exc:
            log.write(f"FAILED {type(exc).__name__}: {exc}\n")
            raise
        for case in report["cases"]:
            log.write(json.dumps(case, ensure_ascii=False, default=str) + "\n")
        log.write("END " + json.dumps(report["summary"], ensure_ascii=False) + "\n")
    report_path = args.output / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {report_path}")
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
