"""Authenticated FastAPI service for the Qwen3 intent adapter."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from algorithm.evaluation.intent_local_model_eval import parse_intent_json
from algorithm.training.promote_intent_adapter import validate_release_manifest
from algorithm.training.verify_adapter_reload import validate_adapter_directory
from fast_api.app.services.llm_intent_classifier import AgentIntentCatalog

SYSTEM_PROMPT = "你是 FitAgent 的意图决策器。只输出符合 IntentDecisionV2 的 JSON，不输出思考过程。"


class IntentRequest(BaseModel):
    schema_version: Literal["intent_decision_v2"] = "intent_decision_v2"
    message: str = Field(min_length=1, max_length=2000)
    rule_decision: dict[str, Any] = Field(default_factory=dict)
    profile_summary: dict[str, Any] = Field(default_factory=dict)


class IntentPredictor(Protocol):
    model_version: str

    def load(self) -> None: ...

    def predict(self, request: IntentRequest) -> tuple[dict[str, Any], dict[str, int]]: ...


class QwenIntentPredictor:
    """Load Qwen3-4B plus a PEFT adapter and generate deterministic JSON."""

    def __init__(self, base_model: str, adapter_path: Path):
        self.base_model = base_model
        self.adapter_path = adapter_path
        self.model_version = f"{base_model}+{adapter_path.name}"
        self._model: Any = None
        self._tokenizer: Any = None
        self._generation_lock = threading.Lock()

    def load(self) -> None:
        validate_adapter_directory(self.adapter_path)
        validate_release_manifest(self.adapter_path)
        try:
            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError as exc:
            raise RuntimeError("install algorithm/training/requirements-training.txt") from exc
        if not torch.cuda.is_available():
            raise RuntimeError("intent adapter inference requires a CUDA GPU")
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=(
                torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            ),
            bnb_4bit_use_double_quant=True,
        )
        tokenizer = AutoTokenizer.from_pretrained(self.base_model)
        base = AutoModelForCausalLM.from_pretrained(
            self.base_model, quantization_config=quantization, device_map="auto"
        )
        self._model = PeftModel.from_pretrained(base, str(self.adapter_path), is_trainable=False)
        self._model.eval()
        self._tokenizer = tokenizer

    def predict(self, request: IntentRequest) -> tuple[dict[str, Any], dict[str, int]]:
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("intent model is not loaded")
        import torch

        context = self._bounded_context(request)
        user_prompt = f"用户问题：{request.message}\n上下文：" + json.dumps(
            context, ensure_ascii=False, separators=(",", ":"), default=str
        )
        inputs = self._tokenizer.apply_chat_template(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
            enable_thinking=False,
        ).to(self._model.device)
        with self._generation_lock, torch.inference_mode():
            output = self._model.generate(
                **inputs,
                max_new_tokens=160,
                do_sample=False,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generated = output[0][inputs["input_ids"].shape[-1] :]
        text = self._tokenizer.decode(generated, skip_special_tokens=True)
        parsed = parse_intent_json(text)
        if parsed.primary_intent not in AgentIntentCatalog.VALID_INTENTS:
            raise ValueError("model returned an unknown primary_intent")
        if any(item not in AgentIntentCatalog.VALID_INTENTS for item in parsed.secondary_intents):
            raise ValueError("model returned an unknown secondary_intent")
        return {
            "primary_intent": parsed.primary_intent,
            "secondary_intents": parsed.secondary_intents,
            "risk_level": parsed.risk_level,
            "needs_clarification": parsed.needs_clarification,
            "confidence": parsed.confidence,
            "reason": parsed.reason,
        }, {
            "prompt_tokens": int(inputs["input_ids"].shape[-1]),
            "completion_tokens": int(generated.shape[-1]),
        }

    @staticmethod
    def _bounded_context(request: IntentRequest) -> dict[str, Any]:
        profile_fields = ("age", "height_cm", "weight_kg", "goal", "experience_level", "injuries")
        rule_fields = (
            "primary_intent",
            "secondary_intents",
            "risk_level",
            "needs_clarification",
            "missing_slots",
        )
        context = {
            "profile": {key: request.profile_summary.get(key) for key in profile_fields},
            "rule_decision": {key: request.rule_decision.get(key) for key in rule_fields},
        }
        encoded = json.dumps(context, ensure_ascii=False, default=str)
        if len(encoded) > 6000:
            raise ValueError("structured context exceeds the inference limit")
        return context


def create_app(
    predictor: IntentPredictor | None = None,
    *,
    inference_key: str | None = None,
) -> FastAPI:
    configured_key = (
        inference_key if inference_key is not None else os.getenv("INTENT_INFERENCE_KEY")
    )
    runtime = predictor or QwenIntentPredictor(
        os.getenv("INTENT_BASE_MODEL", "Qwen/Qwen3-4B"),
        Path(os.getenv("INTENT_ADAPTER_PATH", "adapter")),
    )
    state = {"ready": False, "error": None}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not configured_key:
            state["error"] = "INTENT_INFERENCE_KEY is required"
            raise RuntimeError(state["error"])
        try:
            runtime.load()
            state["ready"] = True
        except Exception as exc:
            state["error"] = f"{type(exc).__name__}: {exc}"
            raise
        yield

    app = FastAPI(title="FitAgent Intent Inference", version="1.0.0", lifespan=lifespan)

    def authorize(authorization: str | None = Header(default=None)) -> None:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not configured_key or not secrets.compare_digest(supplied, configured_key):
            raise HTTPException(status_code=401, detail="Invalid inference credential.")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    def ready() -> dict[str, Any]:
        if not state["ready"]:
            raise HTTPException(status_code=503, detail="Intent model is not ready.")
        return {"status": "ready", "model_version": runtime.model_version}

    @app.post("/v1/intent/classify", dependencies=[Depends(authorize)])
    def classify(request: IntentRequest) -> dict[str, Any]:
        if not state["ready"]:
            raise HTTPException(status_code=503, detail="Intent model is not ready.")
        started = time.perf_counter()
        try:
            decision, usage = runtime.predict(request)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=422, detail="Model returned invalid intent JSON."
            ) from exc
        return {
            "schema_version": "intent_decision_v2",
            "decision": decision,
            "model_version": runtime.model_version,
            "usage": usage,
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }

    return app


app = create_app()
