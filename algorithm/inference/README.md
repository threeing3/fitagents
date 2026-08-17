# Qwen3 intent inference service

This process is intentionally separate from the public FitAgent API. It loads the verified
Qwen3-4B PEFT adapter on a CUDA GPU and exposes only the bounded intent contract.

## Required environment

- `INTENT_INFERENCE_KEY`: private bearer credential shared with the FitAgent deployment.
- `INTENT_ADAPTER_PATH`: directory containing `adapter_config.json` and
  `adapter_model.safetensors`.
- `INTENT_BASE_MODEL`: optional; defaults to `Qwen/Qwen3-4B`.

Install only the isolated training/inference dependencies:

```bash
python -m pip install -r algorithm/training/requirements-training.txt
```

Start only after the adapter reload and frozen evaluation gates have passed. Promotion creates
`fitagent_release_manifest.json` beside the adapter weights:

```bash
python -m algorithm.training.promote_intent_adapter \
  --adapter /path/to/adapter \
  --reload-report /path/to/adapter_reload.json \
  --base-report /path/to/base_eval.json \
  --adapter-report /path/to/adapter_eval.json
```

Then start the service:

```bash
export INTENT_INFERENCE_KEY='<private-value>'
python -m algorithm.inference.serve_intent \
  --adapter /root/autodl-tmp/fitagent/runs/full-800-seed42-v1/outputs/adapter \
  --port 8010
```

Configure the main application with the exact classification URL, not only the host:

```text
ADAPTER_INFERENCE_URL=https://<private-service>/v1/intent/classify
ADAPTER_INFERENCE_KEY=<same-private-value>
```

Health endpoints are unauthenticated for infrastructure probes. Classification requires the
bearer credential. The service refuses startup without a key, a complete adapter, or CUDA.

The public application still applies deterministic safety rules after model inference. A healthy
service proves availability only; it does not prove model quality. Publish quality claims only
from the frozen base-versus-adapter evaluation report.
