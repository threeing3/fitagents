"""Curriculum cards for the project-based algorithm learning mode.

Each card deliberately connects four things: a concept, an implementation,
an experiment, and an interview explanation.  The goal is to prevent passive
copying of commands without understanding the trade-offs behind them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModuleCard:
    module_id: str
    title: str
    track: str
    objective: str
    prerequisites: tuple[str, ...]
    concepts: tuple[str, ...]
    files: tuple[str, ...]
    commands: tuple[str, ...]
    exercises: tuple[str, ...]
    questions: tuple[str, ...]
    acceptance: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


CURRICULUM: tuple[ModuleCard, ...] = (
    ModuleCard(
        "01_data_contracts",
        "数据契约与 Python 工程基础",
        "基础能力",
        "能解释训练样本为什么需要版本、来源、切分和可追踪字段，并能独立扩展一个 Schema。",
        (),
        ("dataclass", "JSONL", "Schema validation", "版本化数据接口"),
        ("algorithm/data/schemas.py", "tests/algorithm/test_data_contracts.py"),
        ("python -m pytest -q tests/algorithm/test_data_contracts.py",),
        (
            "给 TrainingExample 增加一个字段，写出非法输入测试，再说明兼容旧数据的策略。",
            "手工构造一条 tool decision 样本，解释它和普通回复样本的差异。",
        ),
        (
            "为什么不能只保存 prompt 和 response？",
            "Schema 合法率 99% 但业务效果下降时，你先检查什么？",
        ),
        ("测试通过", "能口头解释每个字段的训练/评测用途", "能新增字段而不破坏 round-trip"),
    ),
    ModuleCard(
        "02_dataset_governance",
        "数据治理、脱敏与切分",
        "数据能力",
        "能把 Agent 轨迹变成可信数据集，并识别 PII、重复样本、用户泄漏和合成数据冒充真实效果。",
        ("01_data_contracts",),
        ("PII redaction", "deduplication", "user-level split", "data leakage", "provenance"),
        (
            "algorithm/data/export_traces.py",
            "algorithm/data/sanitize.py",
            "algorithm/data/split_dataset.py",
        ),
        (
            "python -m algorithm.data.export_traces --output algorithm/datasets/manifests/training_examples.jsonl --db local_dev.db --log-dir logs/agent-runs",
            "python -m algorithm.data.validate_dataset algorithm/datasets/manifests/training_examples.jsonl",
        ),
        (
            "修改切分比例并验证同一用户不会跨 train/validation/test。",
            "制造一条带手机号和重复文本的样本，验证脱敏与去重结果。",
            "给每个 source 统计数量，写出真实、规则、教师和合成数据的风险差异。",
        ),
        (
            "为什么不能随机按行切分 Agent 对话？",
            "离线指标提升但线上没有提升，数据治理层可能有哪些问题？",
        ),
        ("Schema 错误为 0", "用户无交叉泄漏", "报告中区分真实数据与合成数据"),
    ),
    ModuleCard(
        "03_intent_and_routing",
        "意图识别与路由基线",
        "应用算法",
        "能把真实请求建模为带安全优先级的多标签任务，识别标签契约、数据泄漏、阈值校准和分布偏移，并设计规则/轻量模型/大模型的公平对比。",
        ("01_data_contracts",),
        (
            "multi-label classification",
            "precision/recall",
            "Macro-F1",
            "calibration",
            "label ontology",
            "distribution shift",
            "data leakage",
        ),
        (
            "docs/learning/03_MULTI_INTENT_SPECIALIST.md",
            "algorithm/app_algorithms/multilabel_intent_baseline.py",
            "algorithm/evaluation/intent_diversity_audit.py",
            "tests/evals/intent_eval_cases.json",
        ),
        ("python -m algorithm.app_algorithms.intent_baseline tests/evals/intent_eval_cases.json",),
        (
            "新增 5 条容易混淆的训练计划/进阶决策样例，观察混淆矩阵变化。",
            "为 injury_or_risk 计算单独 Recall，并解释为什么安全场景不能只看 Accuracy。",
            "设计一个轻量分类器对照实验，保持测试集和标签不变。",
            "解释为什么校准集满分但开发集为零意味着模板分布偏移。",
            "设计一批不读取开发集文本的多来源意图增强请求。",
        ),
        (
            "Macro-F1 和 Accuracy 在类别不均衡时分别说明什么？",
            "为什么风险意图要优先优化 Recall？",
            "规则基线什么时候比大模型更适合做护栏？",
            "阈值校准为什么不能替代语言多样性？",
        ),
        (
            "Macro-F1 和多标签指标分别报告",
            "风险 Recall 单独报告",
            "能解释至少一个分布偏移案例",
            "完成学习手册中的预测题和 60 秒表达",
        ),
    ),
    ModuleCard(
        "04_retrieval_and_reranking",
        "记忆召回、重排序与成本",
        "应用算法",
        "能比较 BM25、向量、混合召回和时间/实体重排序，并同时记录 Recall@K、延迟和 token 成本。",
        ("02_dataset_governance",),
        ("BM25", "embedding retrieval", "hybrid retrieval", "Recall@K", "reranking", "latency"),
        (
            "algorithm/app_algorithms/memory_retrieval_eval.py",
            "tests/evals/retrieval_eval_cases.json",
            "fast_api/app/services/memory_system.py",
        ),
        ("python -m pytest -q tests/algorithm/test_retrieval_eval.py",),
        (
            "构造 10 条记忆和 3 个查询，手算 Recall@1/3/5，再和代码结果对照。",
            "比较只用相似度与加入时间衰减后的排序，写出一个失败案例。",
            "说明为什么 SHA-256 伪向量不能作为正式语义检索结论。",
        ),
        (
            "Recall@5 提升但 P95 延迟翻倍，是否值得上线？",
            "为什么混合召回通常比单一召回稳健？",
        ),
        ("Recall@5 有明确基线", "不使用伪向量得出正式结论", "同时记录 P50/P95 延迟"),
    ),
    ModuleCard(
        "05_tool_planning",
        "工具规划、结构化输出与安全门",
        "应用算法",
        "能把工具选择视为结构化预测问题，区分 Exact Match、顺序准确率、无效工具率和安全硬门。",
        ("03_intent_and_routing",),
        ("structured prediction", "tool sequence", "schema-valid rate", "guardrail", "planner"),
        (
            "algorithm/app_algorithms/tool_plan_eval.py",
            "algorithm/app_algorithms/response_reranker.py",
        ),
        ("python -m pytest -q tests/algorithm/test_algorithm_metrics.py",),
        (
            "为一个训练计划问题画出 intent -> tools -> validation -> response 的状态流。",
            "故意生成一个危险候选，验证回复重排序不能覆盖确定性护栏。",
            "计算 unnecessary-tool rate，并解释它对成本和用户体验的影响。",
        ),
        (
            "为什么工具顺序错误可能比漏掉一个工具更严重？",
            "模型输出 JSON 合法但业务动作错误，如何定位？",
        ),
        ("Schema-valid Rate >= 99%", "安全指标不低于规则基线", "能解释一次工具规划失败"),
    ),
    ModuleCard(
        "06_business_modeling",
        "业务标签、接受率预测与推荐排序",
        "业务算法",
        "能把 Agent 输出连接到接受、执行、依从性和负反馈等结果，并按用户/时间切分训练可解释模型。",
        ("02_dataset_governance", "05_tool_planning"),
        ("label leakage", "Logistic Regression", "AUROC", "calibration", "NDCG@K", "ranking"),
        (
            "algorithm/business/feature_builder.py",
            "algorithm/business/acceptance_model.py",
            "algorithm/business/business_baseline.py",
            "algorithm/evaluation/business_eval.py",
        ),
        (
            "python -m algorithm.business.business_baseline --count 240 --seed 42 --experiment-id business-baseline --output <report.json>",
        ),
        (
            "给 acceptance_model 增加一个多数类对照，解释为什么业务模型必须有简单 baseline。",
            "构造一个时间泄漏特征，观察离线指标虚高并记录原因。",
            "把三个候选回复排序，手算 NDCG@3，并加入安全约束。",
        ),
        (
            "接受率高是否等于用户长期收益高？",
            "AUROC、F1、Calibration 分别适合回答什么问题？",
            "业务标签不足时如何避免包装成真实线上提升？",
        ),
        ("用户级/时间级切分", "与多数类 baseline 对比", "只对模拟结果做模拟声明"),
    ),
    ModuleCard(
        "07_sft_and_dpo",
        "SFT/DPO 后训练闭环",
        "后训练",
        "能解释 SFT 和 DPO 的数据要求、训练目标、Adapter 保存方式和安全回归策略。",
        ("02_dataset_governance", "06_business_modeling"),
        ("instruction tuning", "QLoRA", "LoRA", "preference pair", "DPO", "adapter"),
        (
            "algorithm/training/sft/train_qlora.py",
            "algorithm/training/dpo/train_dpo.py",
            "algorithm/training/configs",
        ),
        (
            "pip install -r algorithm/training/requirements-training.txt",
            "python -m algorithm.training.sft.train_qlora --config algorithm/training/configs/intent_qwen3_4b_qlora.json --dry-run",
        ),
        (
            "手写一对 chosen/rejected，并说明偏好理由不是简单的长度偏好。",
            "设计原模型/SFT/DPO/规则基线四组对照指标。",
            "解释为什么偏好数据不足时不能启动 DPO。",
        ),
        (
            "SFT loss 下降但回复质量不升，可能是什么原因？",
            "DPO 为什么需要 chosen/rejected，而不是只有正例？",
            "如何证明微调没有损害安全指标？",
        ),
        ("配置可复现", "Adapter、数据版本和环境可追踪", "安全指标不低于规则基线"),
    ),
    ModuleCard(
        "08_evaluation_and_interview",
        "评测、实验追踪与面试表达",
        "综合能力",
        "能从假设、数据、指标、实验、结果和限制六个部分讲清楚项目，而不是只展示一个模型分数。",
        (
            "03_intent_and_routing",
            "04_retrieval_and_reranking",
            "06_business_modeling",
            "07_sft_and_dpo",
        ),
        (
            "offline evaluation",
            "ablation",
            "reproducibility",
            "error analysis",
            "model card",
            "causal caution",
        ),
        (
            "algorithm/evaluation",
            "docs/EVALUATION_PROTOCOL.md",
            "docs/MODEL_CARD.md",
            "docs/INTERVIEW_DEMO_SCRIPT.md",
            "logs/experiments",
        ),
        ("python -m compileall -q fast_api tests algorithm", "python -m pytest -q tests/algorithm"),
        (
            "为一个指标提升写出 hypothesis、对照组、测试集、失败分析和下一步。",
            "录制 3 分钟演示：普通问题、计划问题、低分反馈、模型对比。",
            "用 STAR 结构讲一次数据泄漏或安全回归，并说明你如何修复。",
        ),
        (
            "如何证明提升来自算法，而不是测试集变化？",
            "为什么必须同时报告模型指标和业务结果？",
            "项目当前最大的限制是什么？",
        ),
        ("每个实验有 experiment_id", "报告数据版本/代码版本/命令", "能独立完成 3–5 分钟项目叙事"),
    ),
)


MODULES_BY_ID = {card.module_id: card for card in CURRICULUM}


def get_module(module_id: str) -> ModuleCard:
    try:
        return MODULES_BY_ID[module_id]
    except KeyError as exc:
        choices = ", ".join(MODULES_BY_ID)
        raise ValueError(f"unknown module {module_id!r}; choose one of: {choices}") from exc
