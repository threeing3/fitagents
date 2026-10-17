"""Small synthetic live diagnostic; no application DB reads/writes."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fast_api.app.services.intent_decision_engine import IntentDecisionEngine
from fast_api.app.services.model_provider import ModelProvider

CASES = [
    ("stable_preference", "我不喜欢跑步机，平时更喜欢户外骑车。", True),
    ("third_party", "我朋友乳糖不耐受，我想了解这是什么意思。", False),
    ("hypothetical", "如果以后膝盖疼，我应该怎么调整运动？", False),
    ("negated_state", "我今天不疲劳，昨晚睡了八小时。", True),
    ("mixed_request", "我最近膝盖不舒服，把明天的跑步换成骑车。", True),
    ("denied_retention", "我喜欢骑车，但是不要记住这条信息。", False),
]


async def main():
    provider = ModelProvider()
    if not provider.has_live_model():
        raise SystemExit("Live model unavailable; no test was run.")
    results = []
    for case_id, text, expected_nonempty in CASES:
        result = await IntentDecisionEngine(provider).decide(text)
        memory = result.memory_extraction or {"status": "not_run", "candidates": []}
        results.append(
            {
                "case_id": case_id,
                "synthetic_message": text,
                "intent": result.decision.to_dict(),
                "memory_extraction": memory,
                "expected_nonempty": expected_nonempty,
                "count_check": memory.get("status") == "validated"
                and bool(memory.get("candidates")) == expected_nonempty,
                "semantic_review": "manual_review_required_not_accuracy_benchmark",
            }
        )
        print(case_id, memory.get("status"), len(memory.get("candidates", [])), flush=True)
    target = (
        Path(__file__).resolve().parents[1]
        / "logs"
        / ("semantic_memory_live_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".json")
    )
    target.write_text(
        json.dumps(
            {
                "cases": results,
                "provider": provider.settings.llm_provider,
                "model": provider.settings.chat_model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(str(target), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
