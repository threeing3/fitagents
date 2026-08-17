# Qwen3-4B 意图识别训练

## 当前状态

截至 2026-08-17，Intent 04 已完成数据、配置、真实 Qwen3 分词模板、助手回复损失掩码、
不可覆盖运行记录和统一评测入口的本地预检。尚未完成 GPU 训练，因此仓库不声明已有 Adapter，
也不声明 Qwen3-4B 微调后优于底座。

## 固定方案

- 底座：`Qwen/Qwen3-4B`。
- 任务：用户消息与结构化上下文到 IntentDecisionV2 JSON。
- 数据：800 条训练、100 条验证、100 条内部测试；120 条挑战集永久排除。
- 量化：4-bit NF4 双重量化。
- LoRA：`r=16`、`alpha=32`、`dropout=0.05`，覆盖注意力与 MLP 投影层。
- 最大长度：2048；学习率：`2e-4`；2 个 epoch；seed 42。
- Qwen3 思考模式关闭，只对最终助手 JSON Token 计算损失。

官方 Qwen3 模板不带 Transformers 原生助手掩码标记，因此实现使用“助手起始边界”计算标签；
未来模板如果原生提供掩码，则自动优先使用原生结果。真实 Qwen3 分词器对全部 900 条样本的预检结果：

- 无助手标签样本：0；
- 最大总长度：144 Token；
- 中位总长度：117 Token；
- 助手标签长度：45–54 Token；
- 2048 长度截断样本：0。

## 数据生成与 dry-run

```powershell
python -m algorithm.datasets.build_intent_dataset `
  --output algorithm/datasets/manifests/intent_v1_20260817.jsonl `
  --manifest algorithm/datasets/manifests/intent_v1_20260817.summary.json `
  --eval tests/evals/agent_challenge_cases.json `
  --eval tests/evals/intent_eval_cases.json `
  --per-family 50

python -m algorithm.datasets.build_sft_dataset `
  algorithm/datasets/manifests/intent_v1_20260817.jsonl `
  algorithm/datasets/manifests/intent_v1_sft_train_20260817.jsonl `
  --include-split train

python -m algorithm.datasets.build_sft_dataset `
  algorithm/datasets/manifests/intent_v1_20260817.jsonl `
  algorithm/datasets/manifests/intent_v1_sft_validation_20260817.jsonl `
  --include-split validation

python -m algorithm.training.sft.train_qlora `
  --config algorithm/training/configs/intent_qwen3_4b_qlora.json `
  --dry-run
```

## Linux GPU 训练

每次训练必须先创建新的运行 ID，不允许覆盖旧目录：

```bash
python -m pip install -r algorithm/training/requirements-training.txt

python -m algorithm.training.prepare_experiment_run \
  --config algorithm/training/configs/intent_qwen3_4b_qlora.json \
  --run-id smoke-50-seed42-v1 \
  --variant smoke \
  --row-limit 50

python -m algorithm.training.sft.train_qlora \
  --config algorithm/training/configs/intent_qwen3_4b_qlora.json \
  --run-id smoke-50-seed42-v1
```

烟雾训练通过后，使用新的 `full-800-seed42-v1` 运行 ID，省略 `--row-limit` 创建正式运行。
运行目录必须保留命令、环境、GPU、状态、事件、指标、输出清单、Adapter 和校验和。

## 公平评测

原始底座和 Adapter 必须使用相同入口、提示和确定性解码：

```bash
python -m algorithm.evaluation.intent_local_model_eval \
  --dataset tests/evals/agent_challenge_cases.json \
  --base-model Qwen/Qwen3-4B \
  --report-output base-report.json

python -m algorithm.evaluation.intent_local_model_eval \
  --dataset tests/evals/agent_challenge_cases.json \
  --base-model Qwen/Qwen3-4B \
  --adapter research_state/experiments/intent_qwen3_4b_20260817/runs/full-800-seed42-v1/adapter \
  --report-output adapter-report.json
```

报告分别展示原始模型指标和确定性安全合并指标。模型 JSON 无法解析时，原始 Schema 合法率记为失败，
生产安全路径回退到规则；规则收益不得写成微调模型收益。
