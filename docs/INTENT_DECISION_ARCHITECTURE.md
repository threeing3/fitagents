# Intent Decision Architecture

## 目标

FitAgent 将意图识别定义为一次性的 Agent 决策，而不是多个模块各自执行的关键词分类。每个用户回合只生成一个 `IntentDecisionV2`，运行路由、上下文召回、工具规划和 Agent Lab 均消费同一结果。

## 当前调用链

```text
用户消息 + 用户档案
        ↓
IntentRouter 规则基线
        ↓
LLMIntentClassifier（仅复杂请求）
        ↓
规则安全覆盖
        ↓
IntentDecisionV2
        ├── RuntimeRouter
        ├── ContextBuilder
        ├── Planner
        └── Agent trace
```

当前模型提供器由 `LLM_PROVIDER` 决定。使用 DeepSeek 时，复杂请求可以由 DeepSeek 精修；没有密钥、额度耗尽、调用失败或返回非法结构时退回规则决策。意图精修阶段每个回合最多调用一次模型，执行流程选择不再发起第二次模型分类。

## 安全不变量

- 规则识别出的风险等级不能被模型降低。
- 规则识别为 `injury_or_risk` 时，模型不能将其他意图提升为主意图。
- 模型不能重新允许规则已经禁止的计划生成动作。
- 来源记录必须区分模型尝试、模型成功和最终回退，不能根据是否配置密钥推断调用成功。

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
4. Intent 05：通过独立推理服务提供本地模型，并在低置信度时升级至 DeepSeek。
