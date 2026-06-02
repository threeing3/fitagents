# Agent Runtime：Tool Registry + Planner + Executor + Verifier

这份文档解释当前项目里的轻量级 Agent Runtime。它不是为了替代 LangGraph，而是先把工程型 Agent 最重要的能力落地：工具显式注册、动态规划、任务步骤可见、工具调用可追踪、schema 校验、失败重试、确定性修复、运行过程可写入中文日志。

## 主链路

```text
RequestReceived
-> ToolRegistry
-> AgentPlanner
-> AgentTaskTimeline
-> profile.extract
-> memory.verify
-> memory.write
-> context.build
-> plan.decide
-> optional plan.generate
-> optional plan.verify
-> optional plan.repair
-> coach.reply
-> response.verify
-> optional response.repair
-> guardrail.check
-> response.persist
```

## 核心文件

- `fast_api/app/services/agent_runtime.py`：ToolSpec、ToolRegistry、AgentPlanner、AgentExecutor、AgentTaskTimeline。
- `fast_api/app/services/agent_verifier.py`：plan.verify、plan.repair、response.verify、response.repair 的规则实现。
- `fast_api/app/services/coach_agent.py`：把 runtime 接入真实聊天主链路。
- `fast_api/app/services/agent_observability.py`：写入 `logs/agent-runs/*.log` 中文可读日志。
- `web/src/ChatView.tsx`：前端展示当前 Agent 执行过程。

## 当前工具

| 工具 | 副作用 | 说明 |
| --- | --- | --- |
| `profile.extract` | 否 | 抽取档案 patch、开放记忆和纠错信息 |
| `memory.verify` | 否 | 在写入前校验候选记忆和纠错操作，防止伤病等错误记忆污染长期档案 |
| `memory.write` | 是 | 只写入已通过校验的长期记忆和纠错记录 |
| `context.build` | 否 | 构建当前意图需要的上下文包 |
| `plan.decide` | 否 | 判断当前轮是否允许生成计划 |
| `plan.generate` | 是 | 明确请求计划时生成并保存计划 |
| `plan.verify` | 否 | 检查训练计划结构、安全边界和档案约束 |
| `plan.repair` | 是 | 对可修复计划问题做确定性修复 |
| `response.verify` | 否 | 检查回复是否遵守当前请求策略 |
| `response.repair` | 否 | 对回复问题追加自检补充 |
| `guardrail.check` | 否 | 检查医疗、伤病、极端节食等安全风险 |
| `response.persist` | 是 | 保存回复、trace、tool calls 和日志 |

## 动态 Planner

`AgentPlanner` 会先基于当前用户消息识别 intent，再决定本轮计划里是否加入可选工具。

当前 intent 包括：

- `training_plan`
- `training_log`
- `nutrition_advice`
- `recovery_check`
- `injury_or_risk`
- `memory_query`
- `general_chat`

核心原则：

- 当前用户消息是唯一 active instruction。
- 历史消息、长期记忆和 active plan 只能作为背景。
- 普通问答或训练日志不会自动加入 `plan.generate`。
- 只有当前消息明确请求计划时，Planner 才把 `plan.generate`、`plan.verify`、`plan.repair` 加入计划。
- `response.repair` 是条件步骤，只有 `response.verify` 找到可修复问题时才执行。

这让项目更接近 Claude Code 的工作方式：先判断当前目标，再选择工具，而不是每轮固定跑同一套流程。

## Tool Schema + Retry/Repair

每个工具现在可以声明：

- `input_schema`
- `output_schema`
- `retry_count`
- `retry_backoff_ms`
- `permission_level`
- `side_effects`
- `repair_handler`

执行逻辑：

```text
validate input
-> optional input repair
-> execute handler
-> validate output
-> optional output repair
-> retry if allowed
-> write ToolExecutor trace
```

这不是完整 JSON Schema 引擎，而是项目内轻量 schema 校验器，支持：

- required 字段
- object / array / string / integer / number / boolean / null
- enum
- minimum / maximum
- array items
- nested object properties

设计取舍：

- 读类工具可以 retry，例如 `context.build`、`response.verify`。
- 写类工具默认不 retry，避免重复写数据库。
- repair 优先使用确定性修复，不让模型自由修系统状态。
- 所有 attempts、validation_errors、repaired、repair_actions 都进入日志和 Run Detail。

## 日志怎么看

每次对话完成后，会写入：

```text
logs/agent-runs/<timestamp>-<run_id>.log
```

阅读顺序：

1. 看文件开头的 request_id、run_id、用户、会话和最终回复摘要。
2. 看“一、执行时间线”，理解本轮从规划到保存的链路。
3. 找 `AgentPlanner`，看本轮目标和 planned steps。
4. 找 `ToolRegistry`，看工具 schema、retry、side_effects、repair handler 是否注册。
5. 找 `ToolExecutor`，看工具输入摘要、输出摘要、状态、耗时、attempts、validation_errors 和 repair_actions。
6. 找 `MemoryVerifier`，看新候选记忆是否被接受、拒绝或修复。
7. 找 `ContextBuilder`，看本轮加载了哪些记忆、风险、知识和规则。
8. 找 `CurrentRequestPolicy` 和 `PlanGenerationDecision`，判断是否阻止旧命令粘连。
9. 找 `ResponseVerifier`、`ResponseRepair` 和 `GuardrailCheck`，看是否通过自检、安全检查以及是否自动修复。
10. 需要字段级排查时，看“二、完整 JSON（用于深度调试）”。

## 与 Claude Code / Codex 的关系

Claude Code / Codex 这类工程 Agent 的重要模式是：理解目标、规划步骤、调用工具、观察结果、校验输出、必要时修复并记录过程。本项目当前已经具备这个模式的轻量级版本。

需要注意：本项目的 tool 仍然是 AI 私教业务工具，不是 Claude Code 那种文件、终端、浏览器工具。我们借鉴的是工程 Agent 的 runtime 模式，而不是让健身 Agent 操作本地电脑。

后续可以继续补：节点级 resume、工具权限审批、更细的 nutrition.verify、长期训练周期状态和固定 eval regression。
