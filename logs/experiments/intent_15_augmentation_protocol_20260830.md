# 多意图专项：学习闭环与数据增强协议

## 实验动机

v2.1 数据已修复标签覆盖，但规则校准集次意图 Macro-F1 为 1.0、独立开发集为 0，表明模型记住模板而非语义。本阶段不继续根据开发集失败句手工调模板，而是先建立与评测文本隔离、来源可追踪、需要人工审核的数据增强协议，同时将该过程整理为用户可掌握的算法学习模块。

## 修改内容

- 新增多意图专项学习手册，覆盖多标签分类、标签本体、数据泄漏、阈值校准和分布偏移。
- 新增 `IntentAugmentationRequest` 和 `IntentAugmentationOutput` 数据契约。
- 新增不读取开发集和固定测试集文本的增强请求构建器。
- 新增词法多样性与跨集合字符三元组相似度审计。
- 更新学习课程卡和意图模块验收输出。
- 教师生成输出仍未执行；当前文件只包含生成请求，不伪装成训练样本。

## 执行命令

```powershell
.venv\Scripts\python.exe -m pytest tests/algorithm/test_intent_augmentation_contract.py tests/algorithm/test_generate_intent_augmentations.py tests/algorithm/test_learning_mode.py -q --timeout=30
.venv\Scripts\python.exe -m ruff check algorithm/data/intent_augmentation_contract.py algorithm/datasets/build_intent_augmentation_requests.py algorithm/datasets/generate_intent_augmentations.py algorithm/evaluation/intent_diversity_audit.py algorithm/learning/curriculum.py algorithm/learning/mode.py tests/algorithm/test_intent_augmentation_contract.py tests/algorithm/test_generate_intent_augmentations.py
.venv\Scripts\python.exe -m ruff format --check algorithm/data/intent_augmentation_contract.py algorithm/datasets/build_intent_augmentation_requests.py algorithm/datasets/generate_intent_augmentations.py algorithm/evaluation/intent_diversity_audit.py algorithm/learning/curriculum.py algorithm/learning/mode.py tests/algorithm/test_intent_augmentation_contract.py tests/algorithm/test_generate_intent_augmentations.py
.venv\Scripts\python.exe -m algorithm.datasets.build_intent_augmentation_requests --output algorithm/datasets/generated/intent_augmentation_requests_v1_20260830.jsonl --manifest algorithm/datasets/manifests/intent_augmentation_requests_v1_20260830.summary.json --source teacher_generated
.venv\Scripts\python.exe -m algorithm.evaluation.intent_diversity_audit algorithm/datasets/generated/intent_multilabel_v2_1_20260830.jsonl --output algorithm/evaluation/reports/intent_multilabel_v2_1_diversity_20260830.json
.venv\Scripts\python.exe -m algorithm.datasets.generate_intent_augmentations algorithm/datasets/generated/intent_augmentation_requests_v1_20260830.jsonl --output algorithm/datasets/generated/intent_augmentation_teacher_full_v1_20260830.jsonl --report algorithm/evaluation/reports/intent_augmentation_teacher_full_v1_20260830.json --limit 48 --variants 4 --timeout 90
.venv\Scripts\python.exe -m algorithm.datasets.generate_intent_augmentations algorithm/datasets/generated/intent_augmentation_requests_v1_20260830.jsonl --output algorithm/datasets/generated/intent_augmentation_teacher_retry1_v1_20260830.jsonl --report algorithm/evaluation/reports/intent_augmentation_teacher_retry1_v1_20260830.json --retry-report algorithm/evaluation/reports/intent_augmentation_teacher_full_v1_20260830.json --limit 6 --variants 4 --timeout 90
.venv\Scripts\python.exe -m algorithm.evaluation.intent_diversity_audit algorithm/datasets/generated/intent_augmentation_teacher_full_v1_20260830.jsonl algorithm/datasets/generated/intent_augmentation_teacher_retry1_v1_20260830.jsonl --output algorithm/evaluation/reports/intent_augmentation_teacher_diversity_v1_20260830.json
```

## 实验步骤

1. 冻结允许的增强来源和语言现象。
2. 从标签语义简述生成请求，不读取任何开发集或固定测试文本。
3. 分别为 train 与 validation 创建请求，保留来源、Prompt 版本和审核状态。
4. 对现有 v2.1 数据执行词法多样性审计，作为后续增强前基线。
5. 只有教师生成器、解析、去重、来源统计和人工抽检协议完成后，才产生新的训练数据版本。

## 实验结果

- v2.1 规则数据词法审计：精确重复率 0.6322，不同字符三元组占比 0.0521，训练/校准最大三元组 Jaccard 为 0.8611。
- 构建 48 个增强请求，train/validation 各 24 个；7 类语言现象均有覆盖；开发集和固定测试文本访问均为 false。
- 3 请求烟雾生成得到 12 条样本，失败 0，耗时 7.606 秒。
- 完整生成首次运行：48 个请求中 42 个成功，得到 168 条；6 个因教师返回非法 JSON 被隔离，耗时 133.070 秒。
- 修复生成接口为 JSON Object Mode，并增加原始响应摘要与失败请求重试入口。
- `retry1` 仅重试上述 6 个失败请求，得到 24 条，失败 0，耗时 16.590 秒。
- 合计得到 192 条教师生成候选；全部状态为 pending、training_eligible=false，尚未作为训练效果证据。
- 教师候选词法审计：精确重复率 0，不同字符三元组占比 0.7872，训练/校准最大三元组 Jaccard 降至 0.3636、平均值 0.0027。相较规则数据，表层语言多样性明显增加，但该指标不证明标签语义正确。

## 失败原因或下一步计划

当前只证明规则模板在独立开发语言上不能泛化。词法多样性指标只能发现重复和表层相似度，不能证明语义多样性。教师候选虽然格式完整，但仍需对错误标签、标签遗漏、否定误判、语言自然度和安全优先级进行分层抽样审核；审核完成前不得合并进正式训练数据。
