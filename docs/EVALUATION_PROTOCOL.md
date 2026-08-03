# 应用算法与后训练评测协议

## 固定基线

每次实验至少比较：

1. 当前规则/模板基线。
2. 当前 Provider 模型基线。
3. 新的检索、重排序、业务模型或微调模型。

实验必须记录数据版本、代码版本、模型版本、Prompt 版本、规则版本、随机种子和运行命令。

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
