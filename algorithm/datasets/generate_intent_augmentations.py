"""Generate auditable intent paraphrases from frozen augmentation requests."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from algorithm.data.intent_augmentation_contract import IntentAugmentationOutput
from algorithm.data.validate_dataset import read_jsonl
from fast_api.app.core.config import Settings

InvokeTeacher = Callable[[str], str]


def build_prompt(request: dict[str, Any], variants: int) -> str:
    return (
        "你是中文健身意图数据改写器。只根据下面的语义简述生成表达，不得引用任何评测集，"
        "不得增加医学诊断或改变标签语义。"
        f"\n主意图：{request['primary_intent']}"
        f"\n次意图：{json.dumps(request['secondary_intents'], ensure_ascii=False)}"
        f"\n语义简述：{request['semantic_brief']}"
        f"\n语言现象：{request['language_factor']}"
        f'\n请返回严格 JSON：{{"messages":[字符串...]}}，恰好 {variants} 条，彼此明显不同。'
    )


def parse_teacher_messages(text: str, variants: int) -> list[str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    payload = json.loads(cleaned)
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list) or len(messages) != variants:
        raise ValueError(f"teacher must return exactly {variants} messages")
    normalized = [str(message).strip() for message in messages]
    if any(not message for message in normalized) or len(set(normalized)) != variants:
        raise ValueError("teacher messages must be non-empty and unique")
    return normalized


def generate_outputs(
    requests: list[dict[str, Any]],
    *,
    invoke: InvokeTeacher,
    generator_id: str,
    variants: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    outputs: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for request in requests:
        raw_response = ""
        try:
            raw_response = invoke(build_prompt(request, variants))
            messages = parse_teacher_messages(raw_response, variants)
            for message in messages:
                output = IntentAugmentationOutput(
                    request_id=str(request["request_id"]),
                    user_message=message,
                    primary_intent=str(request["primary_intent"]),
                    secondary_intents=tuple(request["secondary_intents"]),
                    source="teacher_generated",
                    generator_id=generator_id,
                    prompt_version=str(request["prompt_version"]),
                )
                errors = output.validate()
                if errors:
                    raise ValueError("; ".join(errors))
                payload = output.to_dict()
                payload["split_target"] = request["split_target"]
                payload["language_factor"] = request["language_factor"]
                outputs.append(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            failures.append(
                {
                    "request_id": str(request.get("request_id")),
                    "error": str(exc),
                    "raw_response_excerpt": raw_response[:500],
                }
            )
    return outputs, failures


def _live_invoker(settings: Settings, timeout: float) -> InvokeTeacher:
    if not settings.has_live_model_key or not settings.chat_base_url:
        raise RuntimeError("a live model key and base URL are required")
    client = httpx.Client(trust_env=False, timeout=timeout)

    def invoke(prompt: str) -> str:
        response = client.post(
            f"{settings.chat_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.chat_api_key}"},
            json={
                "model": settings.chat_model,
                "temperature": 0.9,
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])

    return invoke


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--variants", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--retry-report", type=Path)
    args = parser.parse_args()
    if args.limit <= 0 or args.variants <= 0:
        raise ValueError("limit and variants must be positive")
    settings = Settings()
    requests = read_jsonl(args.requests)
    if args.retry_report:
        retry_payload = json.loads(args.retry_report.read_text(encoding="utf-8"))
        retry_ids = {str(item["request_id"]) for item in retry_payload.get("failures", [])}
        requests = [request for request in requests if str(request["request_id"]) in retry_ids]
    requests = requests[: args.limit]
    started = time.perf_counter()
    outputs, failures = generate_outputs(
        requests,
        invoke=_live_invoker(settings, args.timeout),
        generator_id=f"{settings.llm_provider}:{settings.chat_model}",
        variants=args.variants,
    )
    report = {
        "schema_version": "fitagent-intent-augmentation-generation/v1",
        "requests_attempted": len(requests),
        "outputs_generated": len(outputs),
        "failures": failures,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "generator_id": f"{settings.llm_provider}:{settings.chat_model}",
        "human_review_status": "pending",
        "development_text_access": False,
        "fixed_test_text_access": False,
        "training_eligible": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in outputs),
        encoding="utf-8",
    )
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
