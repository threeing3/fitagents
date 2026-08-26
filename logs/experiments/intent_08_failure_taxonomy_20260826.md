# Intent 08 错误分桶与调优边界

## 实验动机

建立跨规则、DeepSeek 全量调用和混合路由的错误分桶，回答“哪些决策字段在什么场景失败、模型救回了多少规则失败、又引入了多少回退”。固定挑战集只用于诊断，不据此修改路由阈值或生成训练样本。

## 实验契约

- admission_mode：diagnostic。
- method_tier：simplified。
- 数据：固定 120 条挑战测试集的三条已有预测路径。
- 输出：仅聚合指标，不包含用户消息或逐样本预测。
- GPU：不需要；AutoDL 297 机保持关机。
- 成功条件：三条路径 case_id 完全对齐、报告无用户消息、训练资格为 false、给出独立 development 数据契约。

## 修改内容

- 新增失败分析器和契约测试。
- 冻结 plan revision 3 与 `failure-taxonomy-v1` 诊断任务。

## 执行命令、结果和失败记录

1. 对齐三条路径的 120 个 case_id，计算类别、字段通过率、规则失败救回和规则正确回退。
2. 首次契约测试使用字符串搜索 `user_message`，与元数据字段 `contains_user_messages` 冲突而产生 1 个测试失败；报告没有原始文本，修正为检查输入中的哨兵文本不会进入输出。
3. 生成脱敏聚合报告并接入 Agent Lab。
4. 首次 GitHub CI 的 Ruff format dry-run 失败：3 个 Python 文件符合语义检查但未按统一格式排版；执行机械格式化后，定向回归 15 passed。

## 实验结果

- 三条路径的 120 个 case_id 完全对齐，6 个场景各 20 条。
- DeepSeek 全量路径从规则失败中救回 22 条，同时使 3 条规则正确样例回退。
- 混合路径救回 12 条，规则正确样例回退 0 条。
- `multi_intent` 的主要失败字段是 `secondary_intents`：规则精确通过 0%，DeepSeek 30%，混合 10%。
- `missing_parameters` 的主要失败字段是 `clarification`：规则 40%，DeepSeek 65%，混合 45%。
- `safety_bypass` 三条路径精确通过均为 0%，DeepSeek 的主要失败字段仍是 `clarification`；这说明仅增加模型调用不能解决安全追问协议。
- 报告仅含聚合指标，`training_eligible=false`、`contains_user_messages=false`。
- 后端新增分析器契约测试 2 条通过；前端组件测试 2 条、端到端测试 1 条、类型检查和生产构建通过。
- 后端全量回归 623 passed，保留 2 条第三方 Starlette（Web 框架底层组件）弃用警告；Ruff、compileall（Python 编译检查）和报告 JSON（结构化数据格式）校验通过。

## 结论与下一步

- 当前证据支持建立独立 development（开发调优分区）数据集，不支持直接按固定测试集修改路由阈值。
- 下一轮至少为多意图、缺参澄清、安全绕过三个重点类别各构建 30 条全新开发样例，并保持固定 120 条测试集不变。
- 在开发集上比较“字段级置信度路由”和“澄清协议校验器”，通过后只允许一次固定测试集复验。
