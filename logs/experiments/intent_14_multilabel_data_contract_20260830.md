# 多意图专项：标签本体、独立校准集与泛化诊断

## 实验动机

上一阶段确认旧训练集只覆盖开发集 8 个次意图中的 3 个，并包含两个运行时无效主标签。本阶段先修复标签本体和最低样本支持，再建立与开发集隔离的阈值校准集，判断覆盖修复后判别式模型是否具备跨模板泛化能力。

## 修改内容

- 新增 `intent-multilabel-v2.1-20260830` 规则数据工厂。
- 16 个运行时主意图全部进入训练和校准分区。
- 开发集要求的 8 个次意图各有 24 条训练样本和 5 条校准样本。
- 训练与校准按用户和模板族完全隔离。
- 数据审计增加主/次意图最低支持量门禁。
- 判别式基线增加逐标签阈值校准，阈值只能来自 validation 分区。
- 继续禁止使用开发集调阈值，禁止访问固定挑战测试集。

## 执行命令

```powershell
.venv\Scripts\python.exe -m pytest tests/algorithm/test_multilabel_intent_dataset.py tests/algorithm/test_multilabel_intent_baseline.py -q --timeout=30
.venv\Scripts\python.exe -m ruff check algorithm/data/multilabel_intent_dataset_factory.py algorithm/datasets/build_multilabel_intent_dataset.py algorithm/app_algorithms/multilabel_intent_baseline.py algorithm/evaluation/multilabel_data_audit.py tests/algorithm/test_multilabel_intent_dataset.py tests/algorithm/test_multilabel_intent_baseline.py
.venv\Scripts\python.exe -m ruff format --check algorithm/data/multilabel_intent_dataset_factory.py algorithm/datasets/build_multilabel_intent_dataset.py algorithm/app_algorithms/multilabel_intent_baseline.py algorithm/evaluation/multilabel_data_audit.py tests/algorithm/test_multilabel_intent_dataset.py tests/algorithm/test_multilabel_intent_baseline.py
.venv\Scripts\python.exe -m algorithm.datasets.build_multilabel_intent_dataset --output algorithm/datasets/generated/intent_multilabel_v2_1_20260830.jsonl --manifest algorithm/datasets/manifests/intent_multilabel_v2_1_20260830.summary.json --train-per-family 12 --calibration-per-family 5
.venv\Scripts\python.exe -m algorithm.app_algorithms.multilabel_intent_baseline --train algorithm/datasets/generated/intent_multilabel_v2_1_20260830.jsonl --calibration algorithm/datasets/generated/intent_multilabel_v2_1_20260830.jsonl --development algorithm/datasets/development/intent_dev_v1.json --output algorithm/evaluation/reports/intent_multilabel_v2_1_calibrated_20260830.json --threshold 0.5
```

## 实验步骤

1. 从运行时 `AgentIntentCatalog` 生成完整主意图本体。
2. 为目标次意图建立显式组合，并生成独立 train/validation 模板族。
3. 校验数据 Schema、用户隔离、模板族隔离和最低标签支持量。
4. 只在 train 分区拟合字符 TF-IDF 与独立逻辑回归标签头。
5. 只在 validation 分区为各标签选择 F1 最优阈值。
6. 在原有 90 条开发集上执行一次泛化评估，不访问固定测试集。

## 实验结果

- 数据量：训练 576 条、校准 120 条，共 696 条规则生成样本。
- 标签契约：主意图覆盖 16/16，目标次意图覆盖 8/8；最低训练支持量均为 24。
- 隔离检查：用户跨集合泄漏 0，模板族跨集合泄漏 0。
- 数据门禁：`training_ready=true`，仅表示可用于流水线验证，不表示具备模型质量。
- 校准集：次意图 Macro-F1、Micro-F1、Exact Match 均为 1.0。
- 独立开发集：主意图准确率 0.3111；次意图 Macro-F1=0、Micro-F1=0、Exact Match=0.5333。
- Exact Match 主要来自无次意图样本的空集合匹配，不能解释为多意图能力。

## 失败原因或下一步计划

`v2-build-attempt1` 首次构建漏掉 `monthly_review` 和 `recovery_check` 两个次级角色，且固定阈值候选网格未覆盖实际概率范围。修复后生成 v2.1，完整覆盖标签并使用校准预测分数作为候选阈值；旧失败产物保留，不覆盖历史。

v2.1 的校准集满分与开发集零分形成强烈反差，说明当前规则生成数据存在严重模板分布偏移。下一步不应立即微调 Qwen3-4B，也不应继续观察开发集后手工改模板。应先冻结新的数据增强协议，引入不读取开发集文本的独立改写来源，例如人工撰写、教师模型生成与对抗组合，并按来源分别统计；再创建新的不可变数据版本和实验 ID。
