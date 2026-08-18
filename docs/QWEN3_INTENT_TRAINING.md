# Qwen3-4B 意图识别训练

## 当前状态

截至 2026-08-18，Intent 04 已在 AutoDL RTX 4090 上完成 50 条烟雾训练、800 条正式训练、
独立进程 Adapter 重载，以及底座/Adapter 的同入口固定挑战集评测。批准版 Adapter 已下载回本地并通过
SHA-256 校验。该结论只适用于固定离线测试集，不代表线上业务提升或真实用户效果。

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
  --run-id smoke-50-seed42-v3 \
  --variant smoke \
  --snapshot-id <content-addressed-snapshot-id> \
  --row-limit 50

python -m algorithm.training.sft.train_qlora \
  --config algorithm/training/configs/intent_qwen3_4b_qlora.json \
  --run-id smoke-50-seed42-v3
```

烟雾训练通过后，使用新的 `full-800-seed42-v1` 运行 ID，省略 `--row-limit` 创建正式运行。
运行目录必须保留命令、环境、GPU、状态、事件、指标、输出清单、Adapter 和校验和。

训练进程结束不等于验收通过。必须在新的 Python 进程中重新加载底座和 Adapter，完成一次结构化生成：

```bash
python -m algorithm.training.verify_adapter_reload \
  --base-model Qwen/Qwen3-4B \
  --adapter research_state/experiments/intent_qwen3_4b_20260817/runs/smoke-50-seed42-v3/outputs/adapter \
  --output research_state/experiments/intent_qwen3_4b_20260817/runs/smoke-50-seed42-v3/records/adapter_reload_report.json

python -m algorithm.training.verify_experiment_run \
  research_state/experiments/intent_qwen3_4b_20260817/runs/smoke-50-seed42-v3 \
  --require-adapter-reload
```

运行验证器检查状态终点、数据身份、有限指标、日志完整性、Adapter 文件大小与 SHA-256、
新进程重载报告。任一检查失败都不能把运行标记为成功。

AutoDL 远程运行必须使用快照内的相对命令。远程运行器注入 `RESEARCH_RUN_DIR` 和
`RESEARCH_OUTPUT_DIR`；训练脚本将 Adapter 写入后者的 `adapter/`，不覆盖远程运行器维护的
状态、环境、日志或输出清单。下载验证时需要同时保留 `records/` 与 `outputs/` 目录。

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

## 已验证结果

正式运行 `full-800-seed42-v1` 使用 800 条合成训练样本和 100 条验证样本，训练 2 个 epoch：

- 训练耗时 704.11 秒，训练 loss 为 0.1399，验证 loss 为 0.1260；
- Adapter 在全新 Python 进程中成功重载并生成合法结构；
- Adapter 文件大小 132,187,888 字节，SHA-256 为
  `bdb5c8567a27bb6988603a9e54893ec9fd3bb79eafa4cb27c9f788d9b749a8a6`；
- 批准版归档 SHA-256 为
  `c5f365126675459046282fb08d0f7b154af71bb5325d420dc339ff2d64c56d48`。

120 条永久隔离挑战集上的同入口确定性解码结果：

| 指标 | 原始 Qwen3-4B | QLoRA Adapter |
| --- | ---: | ---: |
| 原始结构合法率 | 78.33% | 100.00% |
| 安全合并后完全匹配率 | 5.83% | 13.33% |
| 原始风险召回率 | 37.50% | 100.00% |
| 安全合并后风险召回率 | 65.62% | 100.00% |
| P50 延迟 | 2309.85 ms | 3210.97 ms |
| P95 延迟 | 3188.23 ms | 3630.41 ms |

完全匹配率仍然偏低，说明当前合成训练分布与高难度挑战集之间仍有明显差距；发布批准只表示结构、安全和
相对底座提升门禁通过。线上执行仍采用“确定性规则 → 已验证适配器 → DeepSeek 回退”，安全权威始终属于规则。
