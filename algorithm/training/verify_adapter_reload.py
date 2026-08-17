"""Fresh-process Qwen3 + PEFT adapter reload and generation gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from algorithm.evaluation.intent_local_model_eval import SYSTEM_PROMPT, parse_intent_json


def validate_adapter_directory(adapter_path: Path) -> None:
    required = {"adapter_config.json", "adapter_model.safetensors"}
    missing = sorted(name for name in required if not (adapter_path / name).is_file())
    if missing:
        raise FileNotFoundError(f"adapter is incomplete; missing: {', '.join(missing)}")


def verify_adapter_reload(
    base_model: str, adapter_path: Path, prompt: str = "我胸痛并且呼吸困难，今天还能训练吗？"
) -> dict[str, Any]:
    validate_adapter_directory(adapter_path)
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise RuntimeError(
            "install the isolated training requirements before adapter reload"
        ) from exc
    if not torch.cuda.is_available():
        raise RuntimeError("adapter reload verification requires a CUDA GPU")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    base = AutoModelForCausalLM.from_pretrained(
        base_model, quantization_config=quantization, device_map="auto"
    )
    model = PeftModel.from_pretrained(base, str(adapter_path), is_trainable=False)
    model.eval()
    inputs = tokenizer.apply_chat_template(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
        enable_thinking=False,
    ).to(model.device)
    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=160,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    text = tokenizer.decode(output[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True)
    decision = parse_intent_json(text)
    return {
        "schema_version": "fitagent-adapter-reload/v1",
        "verified": True,
        "base_model": base_model,
        "adapter_path": str(adapter_path.resolve()),
        "generated_text": text,
        "parsed": decision.to_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Qwen3 adapter in a fresh process")
    parser.add_argument("--base-model", default="Qwen/Qwen3-4B")
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt", default="我胸痛并且呼吸困难，今天还能训练吗？")
    args = parser.parse_args()
    report = verify_adapter_reload(args.base_model, args.adapter, args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
