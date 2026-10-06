"""Synthetic-only probe. Defaults to offline; live requires an explicit flag."""

import argparse
import asyncio
import getpass
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fast_api.app.services.jev_intent_client import JevIntentClient  # noqa: E402

CASES = [
    ("multi-task", "记下今天跑了5公里，再帮我看看晚饭怎么吃，别改训练计划。"),
    ("negation", "不要生成训练计划，只解释什么是渐进超负荷。"),
    ("quotation", "朋友说他胸闷，我没有这些症状，只想查询昨天的训练记录。"),
    ("referent", "把那个改成跑步。"),
    ("correction", "昨天那条跑步记录距离写错了，应该是5公里，不是3公里。"),
]
EXPECTED = {
    "multi-task": {
        "training_log": "requested",
        "nutrition_advice": "requested",
        "training_plan": "negated",
    },
    "negation": {"training_plan": "negated", "concept_explanation": "requested"},
    "quotation": {"memory_query": "requested"},
    "referent": {"training_plan": "unclear"},
    "correction": {"workout_correction": "requested"},
}
PRICE_PER_MILLION = 0.042  # Verified 2026-10-04; recheck before future large runs.
MAX_CALL_USD = 64000 * PRICE_PER_MILLION / 1_000_000


def compare_states(case_id, result):
    return {
        "expected_states": EXPECTED[case_id],
        "mismatches": {
            task: {"expected": expected, "actual": result.task_states.get(task)}
            for task, expected in EXPECTED[case_id].items()
            if result.task_states.get(task) != expected
        },
        "check_scope": "specified_boundary_fields_only_not_complete_gold",
    }


async def run(live: bool, budget_usd: float = 0.02, log_dir: Path | None = None) -> None:
    if not math.isfinite(budget_usd) or budget_usd <= 0 or budget_usd > 1:
        raise ValueError("Budget must be positive and at most one dollar")
    key = getpass.getpass("TypeSafe API key (hidden, not saved): ") if live else None
    if live and not key:
        raise ValueError("A key is required for live mode")
    client = JevIntentClient(key)
    log_dir = log_dir or Path(__file__).resolve().parents[1] / "logs" / "experiments"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_dir / f"jev_probe_{stamp}.jsonl"
    # Exclusive creation preserves existing logs. Only fixed synthetic cases are sent.
    with log_path.open("x", encoding="utf-8") as log:

        def record(item):
            log.write(json.dumps(item, ensure_ascii=False) + "\n")
            log.flush()
            print(json.dumps(item, ensure_ascii=True))

        record(
            {
                "event": "start",
                "mode": "live" if live else "offline",
                "budget_usd": budget_usd,
                "price_per_million": PRICE_PER_MILLION,
                "price_verified_date": "2026-10-04",
                "max_cases": len(CASES),
                "data": "synthetic_only",
            }
        )
        reserved = spent = 0.0
        try:
            for case_id, message in CASES:
                if live:
                    # Reserve model's full input limit even if a failed call has no usage.
                    if reserved + MAX_CALL_USD > budget_usd:
                        record({"event": "stop", "reason": "budget_reservation_limit"})
                        break
                    reserved += MAX_CALL_USD
                    result = await client.classify(message)
                    cost = (
                        result.usage.get("input_tokens", 0) * PRICE_PER_MILLION / 1_000_000
                        if result.succeeded
                        else None
                    )
                    spent += cost or 0
                    record(
                        {
                            "case_id": case_id,
                            "synthetic_message": message,
                            **result.summary(),
                            **compare_states(case_id, result),
                            "estimated_cost_usd": cost,
                        }
                    )
                    if not result.succeeded:
                        break
                else:
                    request = client.request_body(message)
                    record(
                        {
                            "case_id": case_id,
                            "mode": "offline_request_check",
                            "model": request["model"],
                            "question_count": len(request["questions"]),
                            "expected_states": EXPECTED[case_id],
                            "exit_authorized": False,
                        }
                    )
        finally:
            record(
                {
                    "event": "end",
                    "estimated_known_cost_usd": spent,
                    "reserved_upper_bound_usd": reserved,
                    "exit_authorized": False,
                }
            )
    print(f"Log: {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Send at most five synthetic requests; no retries"
    )
    parser.add_argument("--budget-usd", type=float, default=0.02)
    args = parser.parse_args()
    asyncio.run(run(args.live, args.budget_usd))
