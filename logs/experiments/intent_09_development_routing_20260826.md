# Intent development routing experiment log

## 实验动机

冻结的 120 条意图测试集已经用于发布评测，不能继续承担调参与错误修复职责。本轮建立独立 development（开发）分区，并验证字段级路由与澄清协议是否能在不接触冻结测试文本的情况下改善困难场景。

## 修改内容

- 构建 90 条独立开发样例，覆盖 `multi_intent`、`missing_parameters`、`safety_bypass` 三类场景。
- 增加精确重合与字符 5-gram Jaccard 相似度隔离门禁。
- 增加字段级置信度路由：主意图阈值 0.80，次意图阈值 0.75；低置信字段请求 DeepSeek 复核。
- 风险等级继续以确定性规则为最低安全权威，不允许模型降低风险。
- 增加澄清协议校验器，对指代不明、伤病细节、渐进证据和训练记录缺失进行阻断或追问。
- 将脱敏汇总报告接入 Algorithm Lab，不公开样例文本或逐条预测。

## 执行命令

```powershell
.\.venv\Scripts\python.exe -m algorithm.evaluation.build_intent_development_set
.\.venv\Scripts\python.exe -m algorithm.evaluation.intent_development_protocol_eval
.\.venv\Scripts\python.exe -m pytest tests\test_field_confidence_router.py tests\test_clarification_protocol.py tests\test_intent_decision_engine.py tests\algorithm\test_intent_development_set.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_field_confidence_router.py tests\test_clarification_protocol.py tests\test_intent_decision_engine.py tests\algorithm\test_intent_development_set.py tests\test_product_security.py -q
cd web
npm run typecheck
npm test -- --run AlgorithmLabView.test.tsx
```

## 实验步骤

1. 从错误分类定义开发场景，使用新的模板族生成开发样例。
2. 与冻结测试集执行规范化精确匹配和字符片段近重复检查。
3. 对规则基线和“规则 + 澄清协议”执行同集诊断。
4. 只发布聚合指标、隔离证明和证据边界。

## 失败记录

第一次定向测试为 8 失败、4 通过。根因是 `ClarificationProtocolValidator` 将 `IntentRouter._dedupe` 实例方法作为静态方法调用，触发缺少参数的 `TypeError`。改为校验器自身的无状态去重方法后，12 项定向测试全部通过。

第一次协议评测因读取隔离报告时使用了错误字段名而失败：代码读取 `exact_overlap_count`，报告实际字段为 `normalized_exact_overlap`。修正字段映射后重新执行成功。失败未删除，保留在本日志中。

## 实验结果

- 开发样例：90 条；冻结测试样例：120 条。
- 规范化精确重合：0。
- 最大字符 5-gram Jaccard：0.0612，低于 0.80 门槛。
- 规则基线精确通过率：0.00%。
- 规则 + 澄清协议精确通过率：6.67%。
- 澄清字段准确率：40.00% → 50.00%。
- 定向后端测试：25 项通过。
- 前端类型检查和 Algorithm Lab 组件测试通过。

## 结论与下一步

澄清协议能够改善一部分缺参数场景，但不能解决主意图、多意图识别和安全绕过问题。当前结果仅是未完成人工审核的开发诊断，不能声明线上效果。下一步应在 Qwen3 意图服务恢复时采集真实字段置信度，并在开发集上校准阈值；完成开发后只能对冻结测试集执行一次发布验证，不能根据测试结果继续调参。
