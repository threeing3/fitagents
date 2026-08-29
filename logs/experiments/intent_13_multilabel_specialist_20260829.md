# 多意图专项：判别式基线与数据可学习性诊断

## 实验动机

Qwen3-4B Adapter 在 v6 的 90 条独立开发集上仅取得 0.1305 的次级意图宏平均 F1。需要区分两种竞争解释：生成式 JSON 架构不适合多标签分类，或者当前训练数据的标签覆盖与运行时契约本身不足。

## 冻结协议

- admission_mode：diagnostic。
- 训练输入：`intent-v1-20260817` 的 train 分区 800 条规则生成样本。
- 评估输入：`intent_dev_v1` 的 90 条独立开发样本。
- 禁止输入：固定 120 条挑战测试集和其他发布测试集。
- 对照：空次级意图、TF-IDF + 独立逻辑回归、Qwen3-4B Adapter v6。
- 主指标：全部次级标签 Macro-F1、Micro-F1、Exact Match。
- 辅助指标：已见标签 Macro-F1、主意图准确率、标签覆盖率和 CPU 延迟。
- 停止条件：发现开发标签未被训练集覆盖，或运行时标签目录与训练标签不一致时，不进入 GPU 微调。

## 修改内容

- 新增训练/开发标签覆盖和运行时目录一致性审计。
- 新增字符级 TF-IDF 与独立二元逻辑回归多标签基线。
- 对训练中从未出现的标签使用恒定零预测，禁止用开发集拟合该标签。
- 分别报告全部标签和训练已见标签，避免用子集指标掩盖不可学习标签。

## 执行命令

```powershell
.venv\Scripts\python.exe -m pytest tests/algorithm/test_multilabel_intent_baseline.py -q
.venv\Scripts\python.exe -m ruff check algorithm/app_algorithms/multilabel_intent_baseline.py algorithm/evaluation/multilabel_data_audit.py tests/algorithm/test_multilabel_intent_baseline.py
.venv\Scripts\python.exe -m algorithm.evaluation.multilabel_data_audit --train research_state/experiments/intent_multilabel_20260829/inputs/intent_v1_all.jsonl --development algorithm/datasets/development/intent_dev_v1.json --output research_state/experiments/intent_multilabel_20260829/runs/label-coverage-audit-retry1/records/data_audit.json
.venv\Scripts\python.exe -m algorithm.app_algorithms.multilabel_intent_baseline --train research_state/experiments/intent_multilabel_20260829/inputs/intent_v1_all.jsonl --development algorithm/datasets/development/intent_dev_v1.json --output research_state/experiments/intent_multilabel_20260829/runs/tfidf-multilabel-baseline-v1/records/baseline_report.json --threshold 0.5
.venv\Scripts\python.exe C:\Users\Lenovo\.codex\skills\research-experiment-lab\scripts\aggregate_results.py research_state/experiments/intent_multilabel_20260829
.venv\Scripts\python.exe C:\Users\Lenovo\.codex\skills\research-experiment-lab\scripts\verify_experiment.py research_state/experiments/intent_multilabel_20260829
```

## 实验步骤

1. 生成固定版本的规则训练数据，不修改开发集。
2. 执行标签覆盖审计。
3. 训练 CPU 判别式基线并在开发集评估。
4. 与 v6 Adapter 的同分区指标比较。
5. 只有数据契约满足可学习性后，才设计 Qwen3-4B 多任务微调。

## 实验结果

- 测试：6 条全部通过；Ruff 静态检查与格式检查通过。
- 数据审计：开发集要求 8 个次级标签，训练集仅覆盖 3 个，标签覆盖率 0.375、样本支持覆盖率 0.5789。
- 不可学习标签：`monthly_review`、`profile_correction`、`profile_update`、`progression_decision`、`training_log`。
- 契约冲突：训练主标签 `exercise_selection`、`workout_logging` 不在运行时标签目录。
- CPU 判别式基线：主意图准确率 0.5222；固定阈值 0.5 下次级意图 Macro-F1 和 Micro-F1 均为 0；Exact Match 为 0.5333，但主要来自空标签匹配，不能解释为有效多意图能力。
- 运行时间：训练与预测合计 3684.437 ms。
- 实验完整性：两项必要运行均通过证据校验，实验状态为 `verified-diagnostic`。
- 未访问固定挑战测试集，未启动 GPU 微调，不产生模型质量或线上提升结论。

## 失败原因或下一步计划

停止门禁已触发：当前数据不具备公平训练多意图模型的条件。下一步先统一运行时标签本体，补齐所有主/次意图的训练覆盖，并建立不接触开发集的独立校准集；之后重新训练判别式基线，校准每个标签的阈值，再决定是否进行 Qwen3-4B 多任务微调。

首次审计运行 `label-coverage-audit-v1` 错误统计了 Manifest 中全部 1000 行，原因是独立审计入口未复用 `split=train` 与 `training_eligible=true` 选择器。修复后以不可变重试运行 `label-coverage-audit-retry1` 记录，旧失败运行保留用于追踪。后续实验聚合一度因两个运行具有相同检索元组而选择旧失败记录；已将修复运行的 variant 更名为 `data-contract-audit-fixed-selector`，不修改任何指标值。
