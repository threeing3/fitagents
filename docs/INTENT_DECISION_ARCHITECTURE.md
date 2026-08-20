# Intent Decision Architecture

## 目标

FitAgent 将意图识别定义为一次性的 Agent 决策，而不是多个模块各自执行的关键词分类。每个用户回合只生成一个 `IntentDecisionV2`，运行路由、上下文召回、工具规划和 Agent Lab 均消费同一结果。

## 当前调用链

```text
用户消息 + 用户档案
        ↓
IntentRouter 规则基线
        ↓
复杂请求？──否──→ 直接使用规则决策
        │是
        ↓
Qwen3-4B QLoRA 独立推理服务
        │失败/超时/未配置
        ↓
DeepSeek LLMIntentClassifier
        ↓
规则安全覆盖
        ↓
IntentDecisionV2
        ├── RuntimeRouter
        ├── ContextBuilder
        ├── Planner
        └── Agent trace
```

复杂请求优先调用受鉴权的 Qwen3-4B 意图适配器；适配器未配置、超时、鉴权失败、服务不可用或返回非法结构时，才进入由 `LLM_PROVIDER` 决定的 DeepSeek 精修。DeepSeek 没有密钥、额度耗尽、调用失败或返回非法结构时退回规则决策。适配器成功后不会再调用 DeepSeek。

意图精修使用 `ModelProvider.intent_model()` 专用客户端。DeepSeek路径关闭思考模式、温度固定为0并限制输出长度；普通回复和工具规划仍使用各自的模型配置，不能共享分类成本结论。

## 安全不变量

- 规则识别出的风险等级不能被模型降低。
- 规则识别为 `injury_or_risk` 时，模型不能将其他意图提升为主意图。
- 模型不能重新允许规则已经禁止的计划生成动作。
- 来源记录必须区分模型尝试、模型成功和最终回退，不能根据是否配置密钥推断调用成功。
- `rules_evaluated` 只表示规则参与决策；`safety_override_applied` 才表示规则真实纠正了模型，并必须记录具体覆盖原因。

## 部署与可观测性

独立服务只接收用户消息、白名单化规则字段和白名单化档案字段。服务通过 Bearer Token 鉴权，启动时验证发布清单、适配器完整性和 CUDA 可用性；健康检查不等同于质量结论。

Agent Lab 的单例诊断展示：规则耗时、适配器状态与耗时、DeepSeek 是否被调用、最终来源、适配器模型版本、Token 用量、失败原因，以及安全覆盖是否真实触发。单例结果明确不计入固定离线指标。

## 版本化契约

`IntentDecisionV2` 包含：

- 主意图和次意图；
- 风险级别与证据；
- 实体、缺失槽位和澄清判断；
- 请求动作、允许动作、禁止动作和候选工具；
- 置信度、任务计划、来源版本和分阶段延迟。

旧模块仍可通过 `to_legacy()` 获取兼容的 `IntentDecision`，但不允许因此重新执行分类。

## 后续演进

1. Intent 02：用固定测试集运行真实 DeepSeek 基线。
2. Intent 03：构建与测试集隔离的数据工厂。
3. Intent 04：将 `local_model_used` 接入 Qwen3-4B 后训练模型。
4. Intent 05：实现独立推理服务和适配器优先、DeepSeek 回退的主应用链路。
5. Intent 06：在真实 GPU 上部署服务，执行鉴权、结构、安全、延迟和主应用端到端验收。
