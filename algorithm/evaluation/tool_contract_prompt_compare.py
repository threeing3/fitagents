"""Paired local diagnostic: only model-visible input schema rendering changes."""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from algorithm.evaluation.local_qwen_model import LocalQwenModel
from algorithm.evaluation.tool_loop_task_eval import CASES, evaluate_task
from fast_api.app.services.llm_agent import LLMAgentService


class LegacyPromptAgent(LLMAgentService):
    """Replay the pre-fix renderer without reverting execution validation."""

    def _simplify_schema(self, schema):
        if not schema:
            return ""
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not required and not properties:
            return ""
        parts = []
        for key in required:
            parts.append(
                key + ": " + str(properties.get(key, {}).get("type", "any")) + " (required)"
            )
        for key in properties:
            if key not in required:
                parts.append(key + ": " + str(properties[key].get("type", "any")) + " (optional)")
        return "{" + ", ".join(parts) + "}"


async def run(output: Path, model=None):
    output.mkdir(parents=True, exist_ok=False)
    model = model or LocalQwenModel()

    def log(message):
        with (output / "run.log").open("a", encoding="utf-8") as handle:
            handle.write(datetime.now(timezone.utc).isoformat() + " " + message + "\n")

    protocol = {
        "revision": "tool-contract-render/v1",
        "variants": ["legacy_lossy", "full_contract"],
        "cases": list(CASES),
        "change": "input_schema rendering only; executor, feedback, scoring and permissions unchanged",
        "model": "Qwen3-4B-Q4_K_M.gguf",
        "temperature": 0,
        "seed": 42,
        "max_tokens": 512,
        "max_live_calls": 40,
        "limitations": [
            "Four previously observed diagnostic cases, not independent held-out accuracy.",
            "Synthetic handlers, no database or write-task success claim.",
            "Dependency reference regenerated per execution; same source and fixed-length distribution.",
            "Counterbalanced variant order, but no cold-cache latency claim.",
        ],
    }
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    log("START paired schema-render diagnostic; no paid API or production database")
    rows = []
    classes = {"legacy_lossy": LegacyPromptAgent, "full_contract": LLMAgentService}
    for index, case_id in enumerate(CASES):
        order = list(classes) if index % 2 == 0 else list(reversed(classes))
        for variant in order:
            log("TASK_START " + case_id + " " + variant)
            try:
                row = await evaluate_task(
                    case_id,
                    model,
                    evidence_mode="local_model_synthetic_tools",
                    agent_class=classes[variant],
                )
            except Exception as exc:
                log("ERROR " + case_id + " " + variant + " " + type(exc).__name__)
                raise
            row["variant"] = variant
            (output / (case_id + "_" + variant + ".json")).write_text(
                json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            rows.append(row)
            log("TASK_END " + case_id + " " + variant + " passed=" + str(row["passed"]))
    pairs = []
    for case_id in CASES:
        pair = {row["variant"]: row for row in rows if row["case_id"] == case_id}
        pairs.append(
            {
                "case_id": case_id,
                "legacy_passed": pair["legacy_lossy"]["passed"],
                "full_contract_passed": pair["full_contract"]["passed"],
            }
        )
    summary = {
        **protocol,
        "completed_executions": len(rows),
        "live_calls": sum(row["live_calls"] for row in rows),
        "passed_by_variant": {
            variant: sum(row["passed"] for row in rows if row["variant"] == variant)
            for variant in classes
        },
        "recoveries": sum(not p["legacy_passed"] and p["full_contract_passed"] for p in pairs),
        "regressions": sum(p["legacy_passed"] and not p["full_contract_passed"] for p in pairs),
        "pairs": pairs,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("END " + json.dumps(summary["passed_by_variant"]))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output)), ensure_ascii=False, indent=2))
