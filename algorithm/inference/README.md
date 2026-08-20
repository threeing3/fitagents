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

Then start the service. On the verified AutoDL instance, the approved paths are:

```bash
read -rsp 'Inference key: ' INTENT_INFERENCE_KEY && export INTENT_INFERENCE_KEY
python -m algorithm.inference.serve_intent \
  --base-model /root/autodl-tmp/weights/huggingface \
  --adapter /root/autodl-tmp/research/fitagent/experiments/intent_qwen3_4b_20260817/runs/full-800-seed42-v1/outputs/adapter \
  --port 8010
```

For a disconnected terminal, start it inside a new `tmux` session and redirect output to the
new deployment run's `run.log`. Do not put the credential in the command, shell history, log,
profile, repository, or process arguments. Expose port 8010 through AutoDL's authenticated custom
service mapping, then use its HTTPS URL as the private service base URL.

Run the deployment gate before configuring the main application:

```bash
read -rsp 'Inference key: ' INTENT_INFERENCE_KEY && export INTENT_INFERENCE_KEY
python -m algorithm.inference.verify_service \
  --base-url https://<private-service> \
  --report research_state/experiments/intent_qwen3_4b_20260817/runs/runtime-service-smoke-v1/verification_report.json
unset INTENT_INFERENCE_KEY
```

The verification report contains only statuses, model version, total latency, and boolean gates.
It never contains the credential, request headers, profile fields, or raw model responses.

Configure the main application with the exact classification URL, not only the host:

```text
ADAPTER_INFERENCE_URL=https://<private-service>/v1/intent/classify
ADAPTER_INFERENCE_KEY=<same-private-value>
```

Health endpoints are unauthenticated for infrastructure probes. Classification requires the
bearer credential. The service refuses startup without a key, a complete adapter, or CUDA.

The main client records bounded operational states: `not_configured`, `timeout`, `unauthorized`,
`service_unavailable`, `invalid_model_output`, and `available`. These statuses are safe to expose
in Agent Lab; response bodies and credentials are not.

The public application still applies deterministic safety rules after model inference. A healthy
service proves availability only; it does not prove model quality. Publish quality claims only
from the frozen base-versus-adapter evaluation report.
