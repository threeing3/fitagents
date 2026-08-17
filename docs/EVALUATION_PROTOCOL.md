# 应用算法与后训练评测协议

## 固定基线

每次实验至少比较：

1. 当前规则/模板基线。
2. 当前 Provider 模型基线。
3. 新的检索、重排序、业务模型或微调模型。

实验必须记录数据版本、代码版本、模型版本、Prompt 版本、规则版本、随机种子和运行命令。

固定评测只能作为发布回归门禁，必须与训练数据物理隔离。当前 `seed_eval` 数据由规则覆盖模板构建，适合发现已知场景回归，但不允许据此声称真实用户分布上的泛化能力。

## 指标

### 应用算法

- 意图 Macro-F1 和准确率。
- 风险 Recall。
- 记忆 Recall@K。
- 工具选择精确匹配率。
- 工具顺序准确率。
- Schema 合法率。
- 不必要工具调用率。
- P50/P95 延迟和 Token 成本。

记忆召回实验必须同时报告 BM25、可用的真实向量分数和混合排序；如果没有真实向量服务或显式向量分数，报告写明 `vector unavailable`，不得用 SHA-256 伪向量替代。

固定发布规模为：意图不少于 120 条、召回不少于 80 个查询、工具规划不少于 200 条、安全与对抗不少于 150 条、回复质量不少于 100 条。工具规划必须同时报告固定链、规则 Planner 和可用时的 LLM Planner；模型未配置时报告不可用，不能补造结果。

### 回复质量

- 安全性、准确性、相关性、完整性、可执行性、忠实性。
- 安全是硬门禁，不允许通过其他维度平均分抵消。

### 业务算法

- 接受率、执行率、7 天依从性。
- 负反馈率、计划修改率。
- Precision、Recall、F1、AUROC、Calibration。
- 推荐排序 NDCG@K。

本地可复现实验：

```powershell
python -m algorithm.business.business_baseline --count 240 --seed 42 --experiment-id business-baseline-v1 --output <report.json>
```

该命令使用 CPU（中央处理器）逻辑回归和多数类基线，按用户切分后报告 AUROC、F1、Brier score（概率预测误差）和 NDCG@5。当前数据由 `synthetic`（合成样本）与 `simulated_outcome`（模拟结果标签）构成，只用于验证方法链路，不能声称真实业务提升。

如果只有合成或模拟结果，报告必须明确标注 `synthetic` 或 `simulated_outcome`。

## 发布门禁

- 风险召回率不低于 98%。
- Schema 合法率不低于 99%。
- 安全指标不得低于确定性基线。
- 新模型没有通过离线测试前，不进入真实流量。

## 阶段三可复现结果

实验 `maturity_03_algorithms_20260809` 的确定性离线基线：

- 固定业务样例 38/38。
- 意图 120 条：Accuracy 1.00、Macro-F1 1.00、风险 Recall 1.00。
- 召回 80 条：BM25 Recall@5 0.95；固定夹具中的混合排序为 1.00；真实向量状态为 `vector unavailable`。
- 工具规划 200 条：固定工具链的选择/顺序准确率为 0、无效额外调用率 0.36；规则 Planner 的选择/顺序/Schema 合法率均为 1.00，额外调用率为 0。
- 安全 150 条，其中风险样例 100 条：风险 Recall 1.00、关键危险建议放行数 0。
- 回复质量 100 条：安全硬门禁通过率 1.00。
- 本次全部为确定性离线评测，Token 成本为 0；未配置 LLM Planner 和真实向量服务。

本结果只代表受控固定集。公开脱敏报告位于 `algorithm/evaluation/reports/maturity_03_baseline.summary.json`；报告生成时工作区尚未提交，因此代码版本带 `+dirty`，合并后应以提交哈希重新生成下一版实验，不能覆盖旧报告。

```powershell
python -m algorithm.evaluation.build_fixed_evals --verify
python -m algorithm.evaluation.run_maturity_gate --experiment-id <new-id> --output <new-report.json>
```
# Agent challenge protocol

`tests/evals/agent_challenge_cases.json` 是只用于测试的高难度挑战集，包含 6 类、120 条固定样例。评测报告同时保留：

- exact pass rate（严格组合通过率）：主意图、次意图、风险级别、是否澄清和必需工具必须全部正确；
- component scores（分项指标）：用于定位具体薄弱环节，不能替代组合门禁；
- failure examples（失败样例）：只来自固定合成挑战集，不展示真实用户内容。

数据文件必须保持 `source=challenge_eval`、`partition=test`、`training_eligible=false`。任何训练导出器都不得吸收该集合。当前报告是调优前基线，不因分数低而修改答案迎合现有规则。

## Intent decision evaluation

意图评测必须明确记录实际被测路径：`rule_only`、`deepseek`、`qwen_base`、`qwen_sft` 或 `hybrid`。规则评测不得描述为大模型评测，模拟提供器结果不得描述为真实 API 结果。

每条预测必须保存 `schema_version`、模型与规则版本、数据版本、调用成功状态和各阶段延迟。DeepSeek 与后续本地模型必须使用相同测试集和相同输出 Schema，至少报告主意图、次意图、风险、澄清、结构合法率、严格组合通过率、延迟和调用成本。

真实意图评测使用专用非思考客户端，避免将长推理Token混入分类成本。`deepseek_all_with_rule_safety` 表示每条样例均调用DeepSeek，但模型结果仍受确定性安全规则约束；它不能简写为纯模型结果。若Token遥测缺失，成本必须报告为不可用，禁止填0。
