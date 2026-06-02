# AI 私教 Agent 开发学习记录

这份文档不是普通变更日志，而是给你以后复盘、学习和面试讲解用的工程笔记。每一节都尽量回答四个问题：为什么要改、改了哪些文件、运行时发生了什么、应该怎么通过日志检查。

## 1. 项目定位

当前项目已经从原始的一次性健身计划 Demo，重构为一个面向普通健身用户的 AI 私教 Agent MVP。

它的核心目标不是“比通用聊天模型更会聊天”，而是展示垂直领域 Agent 的工程能力：

- 通过对话收集用户档案，包括年龄、身高体重、训练目标、训练经验、器械、饮食习惯和健康边界。
- 把稳定信息写入 PostgreSQL，形成 canonical profile 和长期记忆。
- 用 ContextBuilder 按当前意图组合用户档案、长期记忆、健身知识、结构化规则、训练/饮食模板和案例风格。
- 用 DeepSeek/Qwen/OpenAI/offline fallback 这种 provider abstraction 生成回答。
- 用 Agent run trace 和本地中文日志解释 Agent 每一步做了什么、为什么这么做、用了多久、是否触发规则或修复。

一句话：这是一个可记忆、可解释、可观测、可测试的健身领域 Agent，而不是单纯的 prompt demo。

## 2. 当前技术栈

- 前端：React + Vite
- 后端：FastAPI
- 数据库：PostgreSQL + pgvector
- ORM：SQLAlchemy
- 模型层：ModelProvider，支持 DeepSeek、Qwen/DashScope、OpenAI、offline fallback
- Agent runtime：轻量级 Tool Registry + Agent Planner + Agent Executor + Agent Task Timeline
- 知识系统：explanation knowledge、decision rules、plan templates、coaching cases
- 可观测性：AgentRunLogger、agent_runs、tool_calls、`logs/agent-runs/*.log`
- 测试体系：pytest、API smoke test、eval harness 雏形

## 3. 为什么要做长期记忆

最早的问题是：用户已经在前面说过训练经验、健身房器械、甲亢用药、不自己做饭，但 Agent 后面仍然重复询问，甚至把“坐姿推肩”误判成肩伤。

这说明普通聊天上下文不够可靠，必须把信息拆成两类：

- 固定档案：年龄、性别、身高、体重、目标、训练经验、训练频率、器械条件等。
- 长期记忆：健康背景、用药、饮食习惯、训练表现、纠错信息、近期疲劳状态等。

关键设计点：

- 用户当前消息先经过 profile extractor，生成结构化 patch。
- 只有明确的伤病语境才写入 injuries，不能把身体部位直接当伤病。
- “我没有肩伤”“我没说过肩伤”这类纠错会移除 canonical profile 中的错误字段，并写入 correction memory。
- 医疗/用药背景会作为高优先级 memory 进入上下文，但不能让 Agent 直接中断普通建档或训练问答。

相关文件：

- `fast_api/app/services/coach_agent.py`
- `fast_api/app/services/context_builder.py`
- `fast_api/app/db/models.py`
- `fast_api/app/schemas/coach.py`

运行时应该看日志里的这些节点：

- `ProfileExtractorAgent`
- `MemoryAgent`
- `ContextBuilder`
- `CurrentRequestPolicy`

## 4. 为什么不能只做普通 RAG

普通 RAG 文档库适合回答“为什么减脂要吃够蛋白质”这类解释问题，但不适合直接决定用户今天练什么。

健身 Agent 的知识系统被拆成五部分：

- RAG 知识库：解释类知识和 coaching cases，用于解释、类比和风格参考。
- 结构化决策规则库：疲劳高、睡眠差、酸痛明显、医疗风险、训练中断等场景的硬规则。
- 训练/饮食模板库：不同目标、频率、经验、器械条件下的模板。
- 用户长期记忆：用户自己的稳定偏好、风险、历史表现和纠错。
- ContextBuilder：根据当前 intent 决定这轮该加载哪些上下文。

这样设计的原因是：真正影响训练计划和饮食建议的内容必须可检查、可测试，不能完全交给相似度召回和模型自由发挥。

相关文件：

- `fast_api/app/services/fitness_knowledge.py`
- `fast_api/app/data/fitness_knowledge/*.json`
- `fast_api/app/services/context_builder.py`
- `tests/test_fitness_knowledge.py`
- `tests/test_context_builder.py`

日志中应该重点看：

- `KnowledgeRetrieval`：召回了哪些 explanation knowledge 或 coaching case。
- `DecisionRules`：命中了哪些结构化规则。
- `TemplateSelector`：选择了哪些训练或饮食模板。
- `ContextBuilder`：本轮 intent 下最终拼成了什么 context packet。

## 5. 为什么要做 Tool Registry

早期 `CoachAgentService` 是一个 service pipeline：代码直接按顺序调用建档、写记忆、构建上下文、生成回答。它能跑，但不够像工程化 Agent。

问题在于：

- 看不出 Agent 到底有哪些能力。
- 看不出哪些步骤会写数据库。
- 看不出每个工具的输入、输出、耗时和失败原因。
- 后续加入 verifier、repair loop、更多工具后会越来越难维护。

所以新增了 `ToolRegistry`，把隐含在 service 内部的能力显式注册成工具。

当前核心工具：

| 工具 | 是否有副作用 | 作用 |
| --- | --- | --- |
| `profile.extract` | 否 | 从当前消息抽取档案字段、开放记忆和纠错信息 |
| `memory.write` | 是 | 写入长期记忆 |
| `context.build` | 否 | 按当前 intent 构建上下文包 |
| `plan.decide` | 否 | 判断当前轮是否允许生成计划 |
| `plan.generate` | 是 | 当前消息明确要求时生成并持久化计划 |
| `plan.verify` | 否 | 检查计划是否满足结构、安全和档案约束 |
| `plan.repair` | 是 | 对可修复的计划问题做确定性修复 |
| `response.verify` | 否 | 检查最终回复是否符合当前请求约束 |
| `response.repair` | 否 | 对可修复回复问题追加自检补充 |
| `guardrail.check` | 否 | 检查医疗、伤病、极端节食等安全边界 |
| `response.persist` | 是 | 保存回复、trace、tool calls 和日志 |

相关文件：

- `fast_api/app/services/agent_runtime.py`
- `fast_api/app/services/coach_agent.py`
- `tests/test_agent_runtime.py`

日志中应该看：

- `ToolRegistry`：本轮注册了哪些工具，哪些有副作用。
- `ToolExecutor`：每次工具调用的输入摘要、输出摘要、状态和耗时。

## 6. 为什么要做 Planner / Executor

只有 Tool Registry 还不够，因为它只说明“有什么工具”，没有说明“本轮为什么按这些步骤执行”。

所以新增了：

- `AgentPlanner`：根据当前用户消息和可用工具生成本轮执行计划。
- `AgentExecutor`：统一执行工具，并推进 timeline。
- `AgentTaskTimeline`：记录每个步骤从 pending、running 到 completed/failed 的生命周期。

当前主链路接近：

```text
RequestReceived
-> ToolRegistry
-> AgentPlanner
-> AgentTaskTimeline
-> AgentExecutor(profile.extract)
-> AgentExecutor(memory.write)
-> AgentExecutor(context.build)
-> AgentExecutor(plan.decide)
-> optional AgentExecutor(plan.generate)
-> optional AgentExecutor(plan.verify)
-> optional AgentExecutor(plan.repair)
-> coach.reply
-> AgentExecutor(response.verify)
-> optional AgentExecutor(response.repair)
-> AgentExecutor(guardrail.check)
-> response.persist
```

这让项目更接近 Claude Code / Codex 这类工程 Agent 的形态：先规划，再执行，再校验，再必要时修复，最后保存证据。

## 7. 为什么要做 Verifier / Repair Loop

健身 Agent 的风险不是“回答不够好看”，而是：

- 是否错误延续了历史命令。
- 是否把用户没有说过的伤病写进档案。
- 是否给了不适合健康背景的强度建议。
- 是否生成了不完整或不可执行的计划。

所以新增了规则优先的 verifier：

- `plan.verify` 检查训练计划结构、训练日、动作、营养目标、复盘周期、安全提示。
- `plan.repair` 对缺少安全提示、复盘周期、营养目标等可修复问题做确定性修复。
- `response.verify` 检查回复是否只回答当前消息，是否带入旧计划，是否缺少健康边界。
- `response.repair` 对可修复问题追加“Agent 自检补充”。

相关文件：

- `fast_api/app/services/agent_verifier.py`
- `fast_api/app/services/coach_agent.py`
- `tests/test_agent_verifier.py`

面试里可以这样讲：

> 我没有直接相信模型输出，而是在 Agent runtime 中加入了 verifier 和 repair loop。模型生成计划或回复后，会先经过结构化规则校验，检查计划字段、安全边界、当前请求约束和旧命令粘连；可修复问题会由确定性 repair 处理，再进入持久化和前端展示。

## 8. 为什么要做 Current Request Policy

之前出现过一个问题：用户前面要求生成计划，后面只是问别的问题，Agent 又把旧计划带回来了。

根因是历史对话既被当作背景，又被模型误当成仍然有效的命令。

现在通过 `current_request_policy` 约束：

- 当前用户消息是唯一 active instruction。
- 历史消息、长期记忆、active plan 只能作为背景。
- 只有当前消息明确要求“今天练什么/生成计划/制定训练计划”时，才允许生成或展示计划内容。
- 普通问答不能因为历史里出现过计划请求而继续输出计划。

日志中应该看：

- `CurrentRequestPolicy`
- `PlanGenerationDecision`
- `response.verify`
- `response.repair`

## 9. 前端为什么改成 Claude Code 风格过程展示

原来聊天区域会随着消息变长，把当前对话顶出视野；Agent 执行过程也主要在右侧 trace 面板，不够像 Claude Code 那种“当前回答下面能看到正在做什么”的体验。

现在前端改为：

- 整个页面固定在视口高度内。
- 聊天消息区域内部滚动，不让整个页面无限变长。
- 新消息、trace 更新、busy 状态变化时自动滚动到最新位置。
- 当前 assistant 消息下方展示 `Agent 正在思考 / Agent 执行过程`。
- 过程展示只显示可审计的运行阶段，不展示模型隐藏 chain-of-thought。

展示的阶段包括：

- Planner：规划本轮任务。
- Executor：调用工具、构建上下文。
- Verifier：自检输出约束。
- Repair：必要时修复。

相关文件：

- `web/src/ChatView.tsx`
- `web/src/styles.css`

验证命令：

```powershell
docker exec web_ai_fitness_planner npm run build
python -m compileall fast_api\app tests
docker compose restart web_ai_fitness_planner fast_api_ai_fitness_planner
```

## 10. 当前日志怎么看

每次对话会生成：

```text
logs/agent-runs/<timestamp>-<run_id>.log
```

推荐阅读顺序：

1. 看文件开头，确认 run_id、request_id、用户、会话、状态和最终回复摘要。
2. 看“一、执行时间线”，理解本轮完整链路。
3. 找 `AgentPlanner`，看本轮目标和步骤。
4. 找 `ToolExecutor`，看每个工具是否成功、耗时多少、输出了什么摘要。
5. 找 `ContextBuilder`，看 intent、memory、risk、knowledge 是否按预期进入上下文。
6. 找 `CurrentRequestPolicy` 和 `PlanGenerationDecision`，判断有没有旧命令粘连。
7. 找 `KnowledgeRetrieval`、`DecisionRules`、`TemplateSelector`，判断 RAG/规则/模板是否命中正确。
8. 找 `CoachLLM`，看真实模型还是 fallback，以及模型耗时。
9. 找 `ResponseVerifier` 和 `GuardrailCheck`，看是否通过自检和安全检查。
10. 看“二、完整 JSON”，做更细的字段级排查。

日志不会记录 API key。`api_key`、`authorization`、`token`、`password`、`secret` 这类字段会被替换成 `[REDACTED]`。

## 11. 本轮新增的日志改进

本轮把 `AgentRunLogger.write_run_log()` 的可读日志改成真正的中文学习型格式。

改动点：

- 日志开头增加“阅读目标”，明确这份日志该怎么看。
- 元信息改成中文字段：请求 ID、运行类型、用户 ID、会话 ID、运行状态。
- 时间线标题改成“一、执行时间线”。
- 每个节点使用中文字段：节点、状态、耗时、时间、输入摘要、输出摘要、错误。
- 完整结构化数据标题改成“二、完整 JSON（用于深度调试）”。
- 保留完整 JSON，方便以后检查前端 trace、agent_runs.nodes 和 tool_calls。

相关文件：

- `fast_api/app/services/agent_observability.py`
- `tests/test_agent_observability.py`
- `docs/DEVELOPMENT_LEARNING_LOG.zh-CN.md`

## 12. 目前与 Claude Code / Codex 的差距

当前项目已经具备：

- 工具注册：Tool Registry。
- 显式规划：AgentPlanner。
- 执行器：AgentExecutor。
- 时间线：AgentTaskTimeline。
- 输出校验：Verifier。
- 自动修复：Repair。
- 可读日志：AgentRunLogger。
- 前端过程展示：ThinkingProcess。

仍然可以继续借鉴：

- 更强的动态 planning：根据任务动态增删工具步骤，而不是主要依赖固定链路。
- 更完整的 tool schema：让每个工具有更严格的输入输出 schema 校验。
- retry/resume：某个节点失败后从失败节点恢复，而不是整轮重跑。
- run detail 页面：展开每个 tool call 的输入、输出、耗时和错误。
- eval harness 常态化：每次改 Agent 行为都跑固定案例，防止回归。
- 更真实的长期任务状态：把“12 周减脂周期”变成可持续推进的项目状态。

## 13. 面试讲法

可以这样概括：

> 我把一个简单健身计划生成 Demo 重构成了 AI 私教 Agent MVP。系统使用 FastAPI、PostgreSQL/pgvector、React/Vite 和可切换的模型 provider。Agent 不只是调用大模型回答，而是先通过 ProfileExtractor 和 MemoryAgent 建立用户档案与长期记忆，再由 ContextBuilder 按当前意图组合用户记忆、健身知识、结构化规则和训练/饮食模板。运行层实现了轻量级 Tool Registry、Planner、Executor、Timeline、Verifier 和 Repair Loop，每次对话都会生成可读中文日志和数据库 trace，能解释为什么召回这些记忆、命中哪些规则、是否允许生成计划、模型耗时多少以及是否触发安全边界。

如果被问为什么不用 LangGraph：

> 我当前优先实现的是 Agent 工程能力本身：工具抽象、任务步骤、当前请求约束、可观测日志、verifier、repair 和 eval。LangGraph 可以作为后续编排框架引入，但我不想为了框架而框架，所以先用轻量 runtime 跑通可解释、可测试的主链路。

## 14. 后续优先级

1. 把 `agent_runs` 做成前端可展开的 run detail 页面。
2. 给每个工具补更严格的 schema 校验。
3. 增加 `memory.verify`，检查新记忆是否和 canonical profile 冲突。
4. 增加 `nutrition.verify`，检查热量、蛋白质、外食建议是否合理。
5. 把 eval cases 固化到长期回归测试里。
6. 给失败节点增加 retry/resume。
7. 把 12-16 周训练周期做成真正的长期任务状态。

## 15. 2026-06-02：补充聊天窗口体验验证脚本

### 15.1 为什么要补

上一轮已经实现了 Claude Code 风格的聊天体验：

- 页面不再被消息撑得越来越长。
- 聊天消息区作为内部滚动容器。
- 发送消息后立即出现当前用户消息和 assistant 占位。
- assistant 回复逐字追加。
- 最新 assistant 消息位置展示 Agent 正在思考/执行过程。

但当时还有一个问题：浏览器自动化插件的输入能力被虚拟剪贴板限制挡住，导致无法稳定完成“真实输入一条消息”的自动化验证。虽然源码、构建和页面布局读数已经能证明大部分行为，但为了后续学习和回归测试，需要一个可以本地复跑的验证脚本。

### 15.2 新增文件

```text
scripts/verify-chat-ui.ps1
```

这个脚本做三类检查：

1. 源码行为检查  
   检查 `ChatView.tsx` 和 `main.tsx` 是否包含：
   - `messagesRef`
   - 自动滚动到底部
   - `ThinkingProcess`
   - Planner / Executor / Verifier / Repair 四个阶段
   - 发送时插入 user message 和空 assistant message
   - `for (const char of [...text])` 逐字追加

2. 布局约束检查  
   检查 `styles.css` 是否包含：
   - `.app-root { height: 100vh; overflow: hidden; }`
   - `.chat-layout` / `.chat-main` 防止整体撑高
   - `.chat-messages { overflow-y: auto; scroll-behavior: smooth; }`
   - `.thinking-process` 和 active/done 状态样式

3. 本地页面可访问性检查  
   请求 `http://localhost:5173`，确认 Web 页面可访问，并且页面中存在 React root。

### 15.3 日志输出

脚本会把验证过程写入：

```text
logs/experiments/<timestamp>-chat-ui-verify.log
```

日志是中文可读格式，适合学习：

- 哪些检查通过。
- 如果失败，缺少哪个源码片段。
- 本地页面是否能访问。
- 最终结论是什么。

### 15.4 如何运行

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-chat-ui.ps1
```

如果只想做源码检查，不检查本地网页：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-chat-ui.ps1 -SkipWebRequest
```

### 15.5 本次验证结果

运行命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-chat-ui.ps1
docker exec web_ai_fitness_planner npm run build
```

结果：

- 聊天窗口固定、内部滚动、逐字生成、思考过程展示相关检查全部通过。
- 本地 Web 页面 `http://localhost:5173` 可访问。
- Vite 前端构建通过。

本轮的工程意义是：前端体验不再只是“看起来做了”，而是有了一个可以复跑、能写入中文实验日志的验证入口。

## 16. 2026-06-02：补齐 memory.verify 与 Run Detail Debug

### 16.1 为什么要做

之前项目已经有 `profile.extract`、`memory.write` 和可读 agent run 日志，但长期记忆仍然有一个关键风险：

- 模型或规则抽取器可能把“右肩没有伤”误当成“右肩伤病风险”。
- 用户纠错时，如果只做写入，不做写入前校验，错误记忆会进入长期数据库。
- 前端 trace 能看到“正在执行”，但不够方便展开一次 run 的完整节点、工具调用、日志路径和 verifier 结果。

所以本轮目标不是让模型“看起来更会聊”，而是把长期记忆写入前的工程防线补上，并把这个防线暴露到 Run Detail Debug 面板和中文实验日志里。

### 16.2 新增 memory.verify 的职责

新增 `MemoryVerifier` 后，长期记忆链路变成：

```text
profile.extract
-> memory.verify
-> memory.write
```

它的核心原则是：

- 先接收 `ProfileExtractorAgent` 给出的候选记忆和 corrections。
- 如果用户正在纠正伤病，例如“我的右肩没有伤”，就拒绝伤病/风险类候选。
- 只出现身体部位或动作名，不能直接等于伤病。
- 临时状态如果被抽成稳定偏好，会降级成 `recent_state`。
- 甲亢、用药、外食这类非伤病纠错信息，不应被“无肩伤”误伤。

本轮还修了一个真实 smoke 暴露出来的问题：最初 verifier 规则过严，会把同一条消息里的 `medical_context=甲亢/赛治` 和 `nutrition_habit=不自己做饭` 也拒掉。现在已经收窄为只拒绝伤病/风险类候选。

### 16.3 改动文件

后端：

- `fast_api/app/services/memory_verifier.py`  
  新增长期记忆写入前校验器，输出 `passed`、`accepted_candidates`、`rejected_candidates`、`issues`、`repair_actions` 和 `profile_snapshot`。
- `fast_api/app/services/agent_runtime.py`  
  在 `AgentPlanner` 的聊天计划中加入 `memory_verify` 步骤，并确保它位于 `memory_write` 之前。
- `fast_api/app/services/coach_agent.py`  
  把 `memory.verify` 注册进 Tool Registry；流式聊天时先执行 memory.verify，再把已校验结果交给 memory.write。
- `fast_api/app/services/agent_observability.py`  
  增加 `MemoryVerifier` 的 timeline 摘要，日志里可以看到接受/拒绝数量、问题列表和修复动作。

前端：

- `web/src/types.ts`  
  增加 `AgentRunDetail` 类型。
- `web/src/api.ts`  
  增加 `fetchAgentRun(runId)`。
- `web/src/ChatView.tsx`  
  在 trace 区增加 Run Detail Debug 展开面板，显示 run 元信息、日志路径、tool calls、nodes 和 Memory Verify 摘要。
- `web/src/styles.css`  
  增加 Run Detail Debug 面板样式。

测试与验证：

- `tests/test_memory_verifier.py`  
  覆盖肩伤纠错拒绝、临时偏好降级、伤病纠错时仍保留甲亢/外食记忆。
- `tests/test_agent_runtime.py`  
  断言 `memory.verify` 在 planner 中位于 `memory.write` 之前。
- `scripts/verify-memory-run-detail.py`  
  新增端到端 smoke：注册用户、创建会话、发送流式消息、读取 run detail，并写入中文实验日志。

### 16.4 当前真实运行链路

最新真实 API smoke 中，一次聊天 run 的节点大致是：

```text
ToolRegistry
-> AgentPlanner
-> AgentTaskTimeline
-> RequestReceived
-> profile.extract
-> ProfileExtractorAgent
-> memory.verify
-> MemoryVerifier
-> memory.write
-> MemoryAgent
-> IntentRouter
-> context.build
-> ContextBuilder
-> plan.decide
-> CurrentRequestPolicy
-> PlanGenerationDecision
-> KnowledgeRetrieval
-> DecisionRules
-> TemplateSelector
-> CoachLLM
-> response.verify
-> ResponseVerifier
-> optional response.repair
-> guardrail.check
-> ResponsePersisted
```

这比普通 RAG 聊天多了两个工程能力：

- 记忆不是模型说写就写，而是先经过确定性 verifier。
- 每一步都能在 Run Detail 和日志里看到证据。

### 16.5 如何看 Run Detail Debug

在 Web 页面聊天后，右侧 trace 区可以点击：

```text
查看 Run Detail
```

重点看：

1. `Run Detail Debug` 顶部：run id、状态、节点数、工具调用数、日志路径。
2. `Memory Verify` 区域：是否 `passed`、接受了多少候选、拒绝了多少候选、有哪些 issue。
3. `Tool Calls` 区域：是否有 `profile.extract`、`memory.verify`、`memory.write`。
4. `Nodes` 区域：是否存在 `MemoryVerifier` 节点，以及它的输出摘要。

### 16.6 如何复跑本轮验证

命令：

```powershell
python scripts\verify-memory-run-detail.py
```

它会写入：

```text
logs/experiments/<timestamp>-memory-verify-run-detail.log
```

日志里会记录：

- 实验目标和步骤。
- 注册的临时用户和会话。
- 发送的测试消息。
- 流式事件数量和事件类型。
- agent run id 和 agent run 日志路径。
- Run Detail 中的节点列表和工具调用列表。
- `MemoryVerifier` 的完整输出，包括 accepted/rejected/issues/corrections。

最新一次通过日志：

```text
logs/experiments/20260602-182212-memory-verify-run-detail.log
```

关键结果：

- 存在 `MemoryVerifier` 节点。
- 存在 `memory.verify` 工具调用。
- 存在 `memory.write` 工具调用。
- Run Detail 返回了 agent run 日志路径。
- `memory.verify` 接受了甲亢/赛治和外食记忆。
- `memory.verify` 保留了“移除 shoulder 伤病”的 correction。
- 没有把“右肩没有伤”写成新的肩伤风险。

### 16.7 验证命令

本轮运行过：

```powershell
python -m compileall fast_api\app tests
pytest tests\test_memory_verifier.py tests\test_agent_runtime.py tests\test_agent_observability.py -q
docker compose restart fast_api_ai_fitness_planner
python scripts\verify-memory-run-detail.py
```

前端还需要在最终收尾时继续运行：

```powershell
docker exec web_ai_fitness_planner npm run build
```

### 16.8 已知附加问题

`scripts/verify-memory-run-detail.py` 还会顺手读取 dashboard 作为附加诊断。当前 smoke 中 dashboard 返回过 HTTP 500，但它不影响本轮 memory.verify 和 Run Detail 的核心验收，因为：

- 聊天流式接口成功。
- `/v1/agent-runs/{run_id}` 成功。
- `MemoryVerifier` 节点和 tool calls 已经持久化。
- agent run 中文日志已生成。

后续如果要继续做产品可用性收尾，可以单独排查 dashboard 500。

## 17. 2026-06-02：修复训练负重被误识别为身体体重

### 17.1 问题现象

用户在聊天中输入：

```text
今天练胸 我尝试了卧推55KG做组 做了3x5组 然后50kg做2x8组 ...
```

Agent 回复时错误地说：

```text
你目前体重 55kg
```

这说明系统把“卧推 55kg”这种训练负重误写进了 `user_profiles.weight_kg`。

### 17.2 根因

旧规则里有一条过宽的体重抽取：

```text
任意 数字 + kg/公斤 -> weight_kg
```

但在健身对话里，`kg` 至少有两种常见语义：

- 身体体重：我体重80kg、当前体重80kg、身高178cm体重80kg。
- 训练负重：卧推55kg、深蹲100kg、硬拉120kg、哑铃20kg。

如果不做语义消歧，训练日志就会污染 canonical profile。

### 17.3 修复内容

修改文件：

```text
fast_api/app/services/coach_agent.py
tests/test_memory_rules.py
```

新增规则：

- `_extract_body_weight_kg()`  
  只有明确身体体重上下文才抽取 `weight_kg`，例如 `体重80kg`、`weight 80kg`、`178cm，80kg`。
- `_is_training_load_context()`  
  如果 kg 附近出现 `卧推`、`深蹲`、`硬拉`、`做组`、`组`、`RPE`、`bench`、`sets` 等训练语义，就把它作为训练负重候选忽略。
- `_source_text_supports_body_weight()`  
  防止 LLM profile patch 把训练负重补成 `weight_kg`。即使模型返回 `weight_kg=55`，只要原文没有身体体重证据，也不合并进档案。

新增测试：

```text
test_training_load_kg_is_not_extracted_as_body_weight
test_llm_weight_patch_rejected_when_source_text_only_mentions_training_load
test_llm_weight_patch_accepted_when_source_text_mentions_body_weight
```

### 17.4 数据修正

查到被污染用户：

```text
user_id = 6162300b-07b4-4a65-82d9-69b487e2a68c
```

证据：

- 2026-06-01 用户明确说过“我是80KG”。
- 2026-06-02 用户说“卧推55KG做组”后，`user_profiles.weight_kg` 被更新成 55。

已执行非删除式修正：

- `user_profiles.weight_kg` 从 55 修回 80。
- 新增/修正 `correction` 长期记忆：卧推55kg和50kg是训练负重，不是身体体重；当前身体体重应保持为80kg。

### 17.5 验证

运行命令：

```powershell
python -m compileall fast_api\app tests
docker exec fast_api_ai_fitness_planner pytest tests/test_memory_rules.py tests/test_memory_verifier.py -q
docker compose restart fast_api_ai_fitness_planner
```

结果：

```text
22 passed
```

实验日志：

```text
logs/experiments/20260602-184901-weight-disambiguation-fix.log
```

### 17.6 后续方向

这次修复只解决“训练负重不能覆盖身体体重”。更好的下一步是新增：

```text
training_log.extract
```

把这类输入结构化为训练表现：

```json
{
  "exercise": "bench_press",
  "sets": [
    {"weight_kg": 55, "reps": 5, "set_count": 3},
    {"weight_kg": 50, "reps": 8, "set_count": 2}
  ],
  "issue": "post_bench_fatigue_affects_accessory_quality"
}
```

这样它不会污染 profile，反而能进入训练表现记忆，用于后续判断是否该降低卧推主项容量、调整动作顺序或优化训练前碳水。

## 18. 2026-06-02：动态 Planner + Tool Schema + Retry/Repair

### 18.1 为什么要做

之前的 Agent runtime 已经有 `ToolRegistry -> AgentPlanner -> AgentExecutor -> Verifier -> Repair -> Log` 的雏形，但还有两个明显问题：

- Planner 基本是固定链路，每轮都像流水线一样跑，和 Claude Code 那种“先判断任务，再选择工具”的体验还有距离。
- `ToolSpec` 虽然有 `input_schema/output_schema` 字段，但执行时没有真正校验；工具失败后也没有 retry/repair。

这会导致两个后果：

- 面试展示时，容易被追问“你的 tool schema 只是写了字段吗？有没有真正生效？”
- 调试时，只能看到工具成功/失败，看不到输入输出是否合法、有没有自动修复、重试了几次。

### 18.2 借鉴 Claude Code 的哪些能力

这里不是把 Claude Code 的文件/终端/浏览器工具搬进健身 Agent，而是借鉴它的 runtime 模式：

```text
理解当前目标
-> 动态规划步骤
-> 选择可用工具
-> 执行工具
-> 观察工具结果
-> 校验输出
-> 必要时 retry/repair
-> 保存可审计日志
```

也就是说，本项目的 tool 仍然是业务工具，例如 `profile.extract`、`memory.verify`、`context.build`，但它们开始具备更像工程 Agent 的执行协议。

### 18.3 动态 Planner 做了什么

修改文件：

```text
fast_api/app/services/agent_runtime.py
fast_api/app/services/coach_agent.py
```

`AgentPlanner` 新增 intent 判断：

- `training_plan`
- `training_log`
- `nutrition_advice`
- `recovery_check`
- `injury_or_risk`
- `memory_query`
- `general_chat`

Planner 现在会把 intent 写进 `AgentExecutionPlan`。

关键变化：

- 普通训练日志不会自动计划 `plan.generate`。
- 只有当前消息明确请求计划时，才把 `plan.generate`、`plan.verify`、`plan.repair` 加入 planned steps。
- `response.repair` 是条件步骤，只有 `response.verify` 命中 repair actions 时才执行。
- 实际执行时优先使用 Planner 已经创建的 step，避免日志里“计划一套、执行又临时创建一套”。

### 18.4 Tool Schema 做了什么

`ToolSpec` 现在不只是描述工具，还能声明：

```text
input_schema
output_schema
permission_level
side_effects
retry_count
retry_backoff_ms
repair_handler
```

当前轻量 schema 校验支持：

- required 字段
- object / array / string / integer / number / boolean / null
- enum
- minimum / maximum
- array items
- nested object properties

已经给这些工具补了 schema：

- `profile.extract`
- `memory.verify`
- `memory.write`
- `context.build`
- `plan.decide`
- `plan.generate`
- `plan.verify`
- `plan.repair`
- `response.verify`
- `response.repair`
- `guardrail.check`
- `response.persist`

### 18.5 Retry/Repair 做了什么

执行顺序现在变成：

```text
validate input
-> optional input repair
-> execute handler
-> validate output
-> optional output repair
-> retry if allowed
-> record ToolExecutor trace
```

设计原则：

- 读类工具可以 retry，例如 `profile.extract`、`context.build`、`response.verify`。
- 写类工具默认不 retry，避免重复写数据库。
- repair 优先使用确定性修复，不让模型自由修改系统状态。
- 修复和重试都会进入日志：`attempts`、`validation_errors`、`repaired`、`repair_actions`。

新增确定性 repair handler：

- `_repair_profile_extract_tool`
- `_repair_memory_verify_tool`
- `_repair_context_build_tool`
- `_repair_plan_decide_tool`

它们主要负责补齐缺失字段或回退到安全默认值。

### 18.6 日志和 Run Detail 的变化

`AgentRunLogger` 现在会记录：

- ToolRegistry 中每个工具是否有 input schema。
- ToolRegistry 中每个工具是否有 output schema。
- 每个工具的 retry_count。
- 是否有 repair_handler。
- ToolExecutor 的 attempts。
- ToolExecutor 的 validation_errors。
- ToolExecutor 是否 repaired。
- ToolExecutor 的 repair_actions。
- AgentPlanner 的 intent。
- planned step 的 condition。

真实日志例子：

```text
logs/agent-runs/20260602-113447-90a37fd4-be6a-4594-b611-86256f90bab8.log
```

可以看到：

- `ToolRegistry` 输出 12 个工具及其 schema/retry/repair 信息。
- `AgentPlanner` 识别 intent。
- `ToolExecutor` 对每个工具记录 attempts、validation_errors、repaired。
- `response.verify` 命中旧计划粘连和医疗边界问题时，`response.repair` 会追加自检补充。

### 18.7 测试

新增/更新测试：

```text
tests/test_agent_runtime.py
tests/test_agent_observability.py
```

覆盖内容：

- ToolRegistry 正常执行工具。
- schema 错误会返回 `schema_error`。
- transient tool error 会 retry。
- output schema 错误可以被 repair handler 修复。
- Planner 只有在 plan intent 下加入 `plan.generate/plan.verify/plan.repair`。
- 普通训练日志不会加入计划生成工具。
- 日志 compact 会保留 schema/retry/repair 信息。

运行命令：

```powershell
python -m compileall fast_api\app tests
pytest tests\test_agent_runtime.py tests\test_agent_observability.py tests\test_memory_verifier.py -q
docker exec fast_api_ai_fitness_planner pytest tests/test_agent_runtime.py tests/test_agent_observability.py tests/test_memory_verifier.py tests/test_memory_rules.py -q
python scripts\verify-memory-run-detail.py
```

结果：

```text
本机相关测试：13 passed
容器相关测试：32 passed
端到端 smoke：通过
```

最新端到端日志：

```text
logs/experiments/20260602-193854-memory-verify-run-detail.log
```

### 18.8 当前还没有做到什么

这轮已经实现了动态 Planner、Tool Schema、Retry/Repair 的第一版，但还不是完整 Claude Code：

- 还没有节点级 resume。某个工具失败后，不能从失败节点恢复整轮任务。
- 还没有用户审批式权限系统。现在只有 `permission_level` 和 `side_effects` 元信息。
- 还没有复杂工具依赖图。当前 Planner 仍是轻量规则式，不是 LLM 自主规划。
- 还没有把训练日志抽成独立 `training_log.extract` 工具。

但这轮已经让项目从“固定 Agent 流水线”推进到“有显式工具协议、动态工具选择、schema 校验和可观测 retry/repair 的领域 Agent runtime”。
