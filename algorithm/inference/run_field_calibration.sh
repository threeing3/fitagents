#!/usr/bin/env bash
set -euo pipefail

: "${RESEARCH_OUTPUT_DIR:?RESEARCH_OUTPUT_DIR is required}"

export HF_HOME="/root/autodl-tmp/weights/huggingface"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export INTENT_BASE_MODEL="/root/autodl-tmp/weights/huggingface/hub/models--Qwen--Qwen3-4B/snapshots/1cfa9a7208912126459214e8b04321603b3df60c"
export INTENT_ADAPTER_PATH="/root/autodl-tmp/research/fitagent/experiments/intent_qwen3_4b_20260817/runs/full-800-seed42-v1/outputs/adapter"
export INTENT_INFERENCE_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

python -m uvicorn algorithm.inference.intent_service:app --host 127.0.0.1 --port 6006 \
  >"${RESEARCH_OUTPUT_DIR}/service.log" 2>&1 &
service_pid=$!
cleanup() {
  kill "${service_pid}" 2>/dev/null || true
  wait "${service_pid}" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 90); do
  if python -c 'import httpx; raise SystemExit(0 if httpx.get("http://127.0.0.1:6006/health/ready", timeout=2).status_code == 200 else 1)' 2>/dev/null; then
    break
  fi
  sleep 2
done

python -c 'import httpx; response=httpx.get("http://127.0.0.1:6006/health/ready", timeout=5); response.raise_for_status(); print(response.json())'
python -m algorithm.evaluation.intent_adapter_calibration \
  --base-url http://127.0.0.1:6006 \
  --dataset algorithm/datasets/development/intent_dev_v1.json \
  --output-dir "${RESEARCH_OUTPUT_DIR}/calibration" \
  --timeout 45
