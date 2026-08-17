"""Evaluate Qwen3 base or PEFT adapter on frozen intent cases."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.evaluation.intent_live_eval import RISK_ORDER, _checks
from fast_api.app.services.intent_decision import IntentDecision, IntentRouter

SYSTEM_PROMPT = (
    "你是 FitAgent 的意图决策器。只输出 JSON，不输出思考过程。字段必须包含 primary_intent、"
    "secondary_intents、risk_level、needs_clarification 和 reason_codes。"
)


def parse_intent_json(text: str) -> IntentDecision:
    """Parse a strict minimal IntentDecisionV2 prediction."""

    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]).strip()
        if candidate.startswith("json"):
            candidate = candidate[4:].lstrip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model output contains no JSON object")
    payload = json.loads(candidate[start : end + 1])
    primary = payload.get("primary_intent")
    secondary = payload.get("secondary_intents")
    risk = payload.get("risk_level")
    clarification = payload.get("needs_clarification")
    if not isinstance(primary, str) or not primary:
        raise ValueError("primary_intent must be a non-empty string")
    if not isinstance(secondary, list) or not all(isinstance(item, str) for item in secondary):
        raise ValueError("secondary_intents must be a string list")
    if risk not in RISK_ORDER:
        raise ValueError("risk_level is invalid")
    if not isinstance(clarification, bool):
        raise ValueError("needs_clarification must be boolean")
    return IntentDecision(
        primary_intent=primary,
        secondary_intents=secondary,
        risk_level=risk,
        needs_clarification=clarification,
        reason=";".join(str(item) for item in payload.get("reason_codes") or []),
        confidence=0.7,
    )


def merge_deterministic_safety(model: IntentDecision, rule: IntentDecision) -> IntentDecision:
    """Apply only the deterministic safety floor; preserve non-safety model choices."""

    secondary = list(model.secondary_intents)
    primary = model.primary_intent
    risk_level = model.risk_level
    if RISK_ORDER.get(rule.risk_level, 0) >= RISK_ORDER["high"]:
        if RISK_ORDER.get(rule.risk_level, 0) > RISK_ORDER.get(risk_level, 0):
            risk_level = rule.risk_level
        if rule.primary_intent == "injury_or_risk" and primary != "injury_or_risk":
            if primary not in secondary:
                secondary.append(primary)
            primary = "injury_or_risk"
    return IntentDecision(
        primary_intent=primary,
        secondary_intents=secondary,
        risk_level=risk_level,
        needs_clarification=model.needs_clarification,
        reason=model.reason,
        confidence=model.confidence,
    )


def _aggregate(
    rows: list[dict[str, Any]], decisions: list[IntentDecision | None]
) -> dict[str, Any]:
    components: Counter[str] = Counter()
    exact = 0
    valid = 0
    for row, decision in zip(rows, decisions):
        if decision is None:
            continue
        valid += 1
        checks = _checks(row, decision)
        exact += int(all(checks.values()))
        for name, passed in checks.items():
            components[name] += int(passed)
    count = len(rows)
    return {
        "cases": count,
        "schema_valid_rate": round(valid / count, 4) if count else 0.0,
        "exact_match": round(exact / count, 4) if count else 0.0,
        "component_scores": {
            name: round(components[name] / count, 4)
            for name in ("primary_intent", "secondary_intents", "risk_level", "clarification")
        },
    }


def evaluate_predictions(
    rows: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(rows) != len(predictions):
        raise ValueError("prediction count must match evaluation row count")
    router = IntentRouter()
    raw: list[IntentDecision | None] = []
    merged: list[IntentDecision | None] = []
    latencies: list[float] = []
    failures: list[dict[str, Any]] = []
    invalid_fallback_count = 0
    for row, prediction in zip(rows, predictions):
        latencies.append(float(prediction.get("latency_ms") or 0.0))
        try:
            decision = parse_intent_json(str(prediction.get("text") or ""))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raw.append(None)
            merged.append(router.analyze(str(row["user_message"])))
            invalid_fallback_count += 1
            failures.append({"case_id": row.get("case_id"), "error": str(exc)})
            continue
        raw.append(decision)
        merged.append(
            merge_deterministic_safety(decision, router.analyze(str(row["user_message"])))
        )
    ordered = sorted(latencies)
    return {
        "raw_model": _aggregate(rows, raw),
        "with_deterministic_safety": _aggregate(rows, merged),
        "parse_failure_count": len(failures),
        "invalid_output_rule_fallback_count": invalid_fallback_count,
        "parse_failures": failures[:20],
        "latency_ms": {
            "p50": round(statistics.median(ordered), 2) if ordered else 0.0,
            "p95": round(ordered[round((len(ordered) - 1) * 0.95)], 2) if ordered else 0.0,
        },
    }


def run_inference(
    rows: list[dict[str, Any]], base_model: str, adapter_path: Path | None = None
) -> list[dict[str, Any]]:
    """Load the selected variant and generate deterministic JSON predictions."""

    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise RuntimeError(
            "install the isolated training requirements before model evaluation"
        ) from exc
    if not torch.cuda.is_available():
        raise RuntimeError("local model evaluation requires a CUDA GPU")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        base_model, quantization_config=quantization, device_map="auto"
    )
    if adapter_path is not None:
        model = PeftModel.from_pretrained(model, str(adapter_path), is_trainable=False)
    model.eval()
    predictions: list[dict[str, Any]] = []
    for row in rows:
        prompt = tokenizer.apply_chat_template(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": str(row["user_message"])},
            ],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
            enable_thinking=False,
        ).to(model.device)
        started = time.perf_counter()
        with torch.inference_mode():
            output = model.generate(
                **prompt,
                max_new_tokens=160,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        text = tokenizer.decode(
            output[0][prompt["input_ids"].shape[-1] :], skip_special_tokens=True
        )
        predictions.append({"text": text, "latency_ms": (time.perf_counter() - started) * 1000})
    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Qwen3 intent base or adapter")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen3-4B")
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--predictions-input", type=Path)
    parser.add_argument("--predictions-output", type=Path)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]
    if args.predictions_input:
        predictions = [
            json.loads(line)
            for line in args.predictions_input.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        predictions = run_inference(rows, args.base_model, args.adapter)
    if args.predictions_output:
        args.predictions_output.parent.mkdir(parents=True, exist_ok=True)
        args.predictions_output.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predictions),
            encoding="utf-8",
        )
    report = {
        "base_model": args.base_model,
        "adapter": str(args.adapter) if args.adapter else None,
        "source": "fixed_challenge_test",
        "training_eligible": False,
        **evaluate_predictions(rows, predictions),
    }
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
