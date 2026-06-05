# CLAUDE.md — AI 健身私教 Agent

## 项目概述

一个基于 **类 Claude Code Agent 架构**的 AI 私人健身教练平台。Agent 以 LLM 为决策者：system prompt 列出可用工具及 schema，LLM 通过迭代选择工具、观察结果，最终生成回复。同时保留代码驱动的 pipeline 作为回退模式，通过 `USE_LLM_DRIVEN_AGENT` 配置开关实现 A/B 对比。

支持中英双语，完整功能包括：对话式建档、长期语义记忆、结构化健身知识检索、带自我纠错的训练计划生成、安全护栏、Prometheus 可观测性、用户反馈偏好学习、语义缓存、以及自动化的周/月训练回顾。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.115 |
| 数据库 | PostgreSQL + pgvector（开发），SQLite 内存库（测试） |
| ORM | SQLAlchemy 2.0 declarative |
| 数据库迁移 | Alembic 1.14 |
| LLM | DeepSeek v4 Pro（主要），Qwen、OpenAI 兼容 |
| Embedding | Qwen text-embedding-v4 / OpenAI / 离线哈希回退 |
| 认证 | JWT（python-jose + passlib/bcrypt），HS256 |
| 限流 | slowapi 0.1.9 |
| 可观测性 | Prometheus /metrics 端点、AgentRunLogger、LangSmith |
| Agent 运行时 | LLM 驱动的工具调用循环 + 代码驱动的回退 pipeline |
| 安全 | Guardrail 规则引擎（BLOCK/WARN/PASS，10 条规则，上下文感知） |
| Prompt 管理 | YAML PromptRegistry（23 个 Prompt，版本化，热重载） |
| 上下文管理 | 基于 Token 估算的 ContextWindowManager，自动压缩 |
| 前端 | React 18 + TypeScript，Vite，Claude Code 风格的 agent 过程卡片 |
| CI/CD | GitHub Actions（lint、type-check、test matrix、Docker build、OpenAPI 校验） |
| 容器 | Docker Compose（PostgreSQL 16 + pgvector，FastAPI --reload，Vite dev） |

## 目录结构

```
ai-fitness-planner/
├── .github/workflows/
│   ├── ci.yml
│   └── deploy.yml
├── alembic/
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│       ├── 001_initial_schema.py   # 34 张表，13 个索引，pgvector Vector 列
│       ├── 002_feedback.py         # UserFeedback 用户反馈表
│       └── 003_semantic_cache.py   # SemanticCache 语义缓存表 + IVFFlat 索引
├── docs/
│   ├── AGENT_RUNTIME.md                     # Agent Runtime 架构文档
│   ├── ENGINEERING_IMPROVEMENTS.md          # 护栏/Prompt/指标/反馈/缓存/回顾
│   ├── LLM_DRIVEN_AGENT_ARCHITECTURE.md     # LLM 驱动 Agent 架构设计文档
│   └── DEVELOPMENT_LEARNING_LOG.zh-CN.md
├── docker-compose.yml
├── fast_api/
│   ├── requirements.txt
│   └── app/
│       ├── main.py              # FastAPI 入口，/health，/metrics
│       ├── api/
│       │   ├── auth_api.py          # 注册/登录/个人信息
│       │   ├── coach_platform.py    # 聊天/建档/Check-in/训练/计划/Dashboard/回顾
│       │   ├── eval_api.py          # 评测框架
│       │   ├── feedback_api.py      # 用户反馈提交/统计
│       │   ├── memory_api.py        # 记忆 CRUD/搜索/上下文构建
│       │   └── nutrition_api.py     # 食物识别+营养日志
│       ├── core/
│       │   ├── auth.py           # JWT 签发与校验
│       │   ├── config.py         # Pydantic Settings，含 USE_LLM_DRIVEN_AGENT
│       │   ├── errors.py         # 自定义异常+统一错误处理
│       │   ├── eval_metrics.py   # 评测指标
│       │   ├── guardrails.py     # 安全护栏（10 条规则，~1.5ms）
│       │   ├── metrics.py        # Prometheus 指标（Counter/Gauge/Histogram，15 个）
│       │   ├── prompts.py        # PromptRegistry — 集中管理，版本化，线程安全，热重载
│       │   ├── retry.py          # 指数退避重试
│       │   └── security.py       # 密码哈希
│       ├── data/
│       │   ├── prompts.yaml              # 23 个版本化 Prompt
│       │   └── fitness_knowledge/
│       │       ├── decision_rules.json        # 25 条决策规则
│       │       ├── plan_templates.json        # 19 个训练计划模板
│       │       ├── explanation_knowledge.json # 30 条运动科学知识
│       │       ├── coaching_cases.json        # 21 个教练案例
│       │       └── eval_cases.json            # 31 个评估用例
│       ├── db/
│       │   ├── database.py       # Engine、SessionLocal、init_db（Alembic 优先）
│       │   └── models.py         # SQLAlchemy 模型
│       ├── schemas/
│       │   └── agent.py          # Pydantic 请求/响应 schema
│       └── services/
│           ├── agent_observability.py   # AgentRunLogger（结构化日志）
│           ├── agent_runtime.py         # ToolSpec/ToolRegistry/AgentPlanner/Executor/Timeline
│           ├── agent_verifier.py        # AgentVerifier（计划/回复校验与修复）
│           ├── coach_agent.py           # 主编排器（LLM 驱动 / 代码驱动双模式调度）
│           ├── context_builder.py       # IntentRouter + ContextBuilder
│           ├── context_window_manager.py # Token 感知上下文管理与自动压缩
│           ├── decision_logger.py       # Agent 决策持久化
│           ├── eval_service.py          # 评测编排
│           ├── feedback_learner.py      # 反馈收集器/偏好学习器/Prompt 增强器
│           ├── feedback_learner_integration.py # 反馈增强 Prompt 接入点
│           ├── fitness_knowledge.py     # 结构化知识系统
│           ├── fitness_math.py          # 宏量营养素计算
│           ├── llm_agent.py             # LLM 驱动 Agent 服务（Claude Code 模式）
│           ├── memory_system.py         # MemoryManager
│           ├── memory_verifier.py       # MemoryVerifier（防伤病误判）
│           ├── model_provider.py        # LLM/Embedding 抽象层 + Vision
│           ├── nutrition_service.py     # 食物识别+营养处理
│           ├── plan_reviewer.py         # 周/月训练回顾
│           └── semantic_cache.py        # pgvector 语义缓存
├── tests/
│   ├── conftest.py
│   ├── test_agent_observability.py
│   ├── test_api_integration.py      # 21 个 FastAPI TestClient 集成测试
│   ├── test_context_builder.py      # 18 个意图分类+上下文构建测试
│   ├── test_context_window.py       # Token 估算+预算+压缩测试
│   ├── test_decision_rules.py       # 24 个规则匹配测试
│   ├── test_eval_cases.py           # 评测用例测试
│   ├── test_eval_framework.py       # 评测框架测试
│   ├── test_feedback.py             # 反馈系统测试
│   ├── test_fitness_knowledge.py    # 26 个知识检索测试
│   ├── test_fitness_math.py         # 营养计算测试
│   ├── test_guardrails.py           # 49 个护栏规则测试
│   ├── test_memory_rules.py
│   ├── test_memory_system.py
│   ├── test_metrics.py              # Prometheus 指标测试
│   ├── test_migrations.py           # Alembic 迁移测试
│   ├── test_nutrition.py            # 食物识别测试
│   ├── test_plan_reviewer.py        # 计划回顾测试
│   ├── test_prompts.py              # Prompt 注册中心测试
│   └── test_semantic_cache.py       # 语义缓存测试
└── web/
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── main.tsx           # App 入口（AuthProvider 鉴权 + NDJSON 流）
        ├── api.ts             # API 客户端（自动注入 JWT）
        ├── AuthContext.tsx
        ├── LoginView.tsx
        ├── ChatView.tsx        # 聊天界面（含类 Claude Code agent 过程卡片）
        ├── DashboardView.tsx
        ├── CheckinView.tsx
        ├── AccountView.tsx
        ├── types.ts
        └── styles.css          # Tailwind + dark theme
```

---

## 开发日志

以下按时间顺序记录本项目最近所有重大改动。

### 2026-06-04：安全护栏 + Prompt 工程化 + Alembic 迁移 + 四个加分项

**安全护栏（guardrails.py）**
- 创建 `core/guardrails.py`：10 条规则函数，三层严重级别（BLOCK / WARN / PASS）
- 上下文感知匹配：伤病术语和诊断性表述必须在 50 字符窗口内同时出现才触发 BLOCK，避免误伤
- 总延迟 < 1.5ms，相对 LLM 调用 1500ms 几乎零开销
- 集成到 `handle_chat_message`、`stream_chat_message`、`stream_chat_events` 三条消息路径
- 每个返回结果携带 `guardrail` 字段（action / passed / flags）
- BLOCK 级别的替换文案从 prompt registry 动态加载
- 49 个测试（test_guardrails.py）

**Prompt 工程化（prompts.py + prompts.yaml）**
- 将分散在 5 个 Python 文件中的 17 个硬编码 prompt 字符串全部提出，集中到 `data/prompts.yaml`
- 每个 prompt 有 version、description、last_modified 元数据
- 创建 `core/prompts.py`：PromptRegistry 类，线程安全，读写分离（get() 无锁，reload() 加锁）
- 模块级单例 `registry = PromptRegistry()`
- 支持 `.format()` 模板替换
- 20 个测试（test_prompts.py）

**Alembic 数据库迁移**
- 替换了 `database.py` 中 65 行手写的 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 语句
- 创建 `001_initial_schema.py`：34 张表的完整 DDL，13 个索引，pgvector Vector 列
- `init_db()` 改为 `alembic upgrade head` 优先，失败回退到 `Base.metadata.create_all`
- 13 个测试（test_migrations.py）

**四个加分项**
1. **Prometheus 可观测性（metrics.py）**
   - 零外部依赖，自建 Counter/Gauge/Histogram 原语（204 行）
   - 15 个预定义指标覆盖 LLM、缓存、护栏、API、Agent、业务
   - `GET /metrics` 端点，标准 Prometheus 文本格式
   - `track_llm_call()` 工厂函数，流式和非流式 LLM 调用点均已集成
   - 修复了 Histogram `collect()` 的累积计数 bug
   - 33 个测试（test_metrics.py）

2. **用户反馈闭环（feedback_api.py + models.py）**
   - UserFeedback 模型（评分 1-5，分类，评论，回复快照）
   - API：POST /v1/feedback（upsert）、GET /v1/feedback/stats（30 天聚合）、GET /v1/feedback（分页）
   - `_save_message` 现在返回 ChatMessage 对象，`handle_chat_message` 返回含 `feedback_message_id`
   - Alembic 迁移 002_feedback.py
   - 15 个测试（test_feedback.py）

3. **语义缓存（semantic_cache.py）**
   - pgvector 余弦相似度缓存 LLM 回复
   - 相似度阈值 0.95，TTL 24 小时
   - 集成到 `_coaching_reply` 和 `_coaching_reply_stream`
   - Alembic 迁移 003_semantic_cache.py，含 IVFFlat 索引
   - 18 个测试（test_semantic_cache.py）

4. **定期计划回顾（plan_reviewer.py）**
   - 聚合训练日志、Check-in、身体数据、恢复数据
   - 计算依从率、RPE 趋势、睡眠/精力/酸痛平均、体重变化、风险信号
   - LLM 生成评审（有 live model 时），规则回退（无 live model 时）
   - API：POST /v1/plans/review?period_days=7 或 30
   - 12 个测试（test_plan_reviewer.py）

---

### 2026-06-04：Agent Runtime + 前后端统一

**Agent Runtime 架构（agent_runtime.py + agent_verifier.py + memory_verifier.py）**
这是本次迭代最核心的架构变更，将项目从一个"手动调函数"的聊天机器人升级为具有 Claude Code 风格工具执行管道的 Agent。

- 创建 `agent_runtime.py`：ToolSpec、ToolRegistry、AgentPlanner、AgentExecutor、AgentTaskTimeline
  - 13 个业务工具统一注册（profile.extract、memory.verify、memory.write、context.build、plan.decide、plan.generate、plan.verify、plan.repair、coach.reply、response.verify、response.repair、guardrail.check、response.persist）
  - 每个工具有：input_schema / output_schema / permission_level / side_effects / retry_count / retry_backoff_ms
  - Schema 校验在输入和输出两端执行，失败先走 repair handler，不行再重试
  - 写类工具不重试（side_effects 标记），避免重复写库

- 创建 `agent_verifier.py`：AgentVerifier
  - plan.verify：检查训练天数、频率是否匹配用户档案、是否缺少疼痛/安全提示
  - plan.repair：确定性修复（追加安全文本、补全营养建议）
  - response.verify：检查回复是否遵守当前请求策略
  - response.repair：有条件执行，只有当 verify 发现问题时才追加修复文本
  - 核心原则：repair 必须是确定性的，不让 LLM 自由修改系统状态

- 创建 `memory_verifier.py`：MemoryVerifier
  - 写入前校验：空内容拒绝、伤病纠错矛盾检测、身体部位无伤病上下文降级、档案冲突检测
  - 返回 accepted / rejected 候选列表

- 创建 `agent_observability.py`：AgentRunLogger
  - 收集结构化 node/event trace
  - 按 run 写入可读中文日志 `logs/agent-runs/<timestamp>-<run_id>.log`
  - 日志包含：执行时间线、Planner 目标、工具 schema/重试/side_effects、MemoryVerifier 结果、ResponseVerifier 自检、Guardrail 结论

**前后端 Agent 可视化（ChatView.tsx + styles.css）**
- 将原有的侧边栏 Trace Panel 改为 Claude Code 风格的**内联 Agent 过程卡片**
- 进度条展示（"正在思考" / "已完成"，步骤计数）
- 三阶段分组：计划建档 → 上下文回复 → 验证修复
- 每步状态：已完成（绿色 ✓）、执行中（旋转动画）、等待中（灰点），附带延迟 ms
- 可折叠的完整 Run Detail（工具调用链 + 状态 + 延迟）
- NDJSON 事件流实时渲染：step、tool_call、status、error、answer_delta、done

---

### 2026-06-04：流式/非流式路径统一 + 上下文窗口管理

**非流式路径接入 Agent Runtime**
- 原来的 `handle_chat_message` 直接裸调底层服务，没有经过 Agent Runtime
- 重写为 `_handle_chat_code_driven`：使用与流式路径相同的 ToolRegistry → AgentPlanner → AgentExecutor → Timeline 管道
- 非流式路径收集事件到内部列表（不做 NDJSON yield），最后统一返回结果 dict
- 消除流式和非流式的行为不一致

**上下文窗口管理（context_window_manager.py）**
- Token 估算：3.5 chars/token（中英混合的保守估算）
- 预算分配：System(5%)、Profile(3%)、Plan(10%)、Risk(2%)、Memory(20%)、Knowledge(15%)、History(30%)、Output(15%)
- 自动压缩：按重要性排序记忆（重要性高优先），按时间排序对话历史（最新优先），超出预算时裁掉最低优先级的条目
- 安全上下文永不裁剪（risk notes、guardrail flags）
- 支持 GPT-4o(120K)、Claude(180K)、DeepSeek(64K)、Qwen(32-128K) 多模型窗口

---

### 2026-06-04：LLM 驱动 Agent 架构

这是架构升级的核心改动——将 Agent 从代码驱动翻到 LLM 驱动，真正对标 Claude Code 模式。

**新建 `llm_agent.py`**
- `LLMAgentService` 类，实现完整的 LLM 工具调用循环
- 执行流程：
  1. 构建 system prompt（含所有 13 个工具的 schema + 描述 + 使用提示）
  2. 发送 [system_prompt, user_message] 给 LLM
  3. 解析 LLM 回复中的 `<tool_call>` JSON 块
  4. 找到 tool_call → Host 执行工具 → 注入 `<tool_result>` 回对话 → 回到步骤 2
  5. 没有 tool_call → 当前文本就是最终回复 → 退出循环
  6. 最多 10 轮迭代
- 工具结果以 HumanMessage 注入对话（模拟 Host 角色，与 Claude Code 一致）
- 每次注入工具结果前检查上下文是否超过 75% 模型窗口，超过则压缩早期消息为摘要
- System prompt 动态构建自当前 ToolRegistry（不是硬编码）
- 支持 `<tool_call>` JSON 解析（含 markdown 代码块清理 + 正则回退）
- `LLMAgentResult` dataclass 封装完整结果（final_response / tool_calls / nodes / iterations / tokens / latency / guardrail）

**修改 `coach_agent.py`**
- 添加 `USE_LLM_DRIVEN_AGENT` 配置开关（`get_settings().use_llm_driven_agent`）
- `handle_chat_message` 现在作为调度器：根据配置走 LLM 驱动或代码驱动
- `_handle_chat_llm_agent`：非流式 LLM Agent 处理器
- `_stream_chat_llm_agent`：流式 LLM Agent 处理器（NDJSON 事件 + answer_delta 字符流）
- 两个处理器均：创建 LLMAgent → 调用 run() → guardrail 检查 → 持久化 AgentRun + ToolCall → 写日志

**修改 `config.py`**
- 新增 `use_llm_driven_agent: bool` 配置项（默认 false，通过 `USE_LLM_DRIVEN_AGENT` 环境变量控制）

**架构设计文档**
- 创建 `docs/LLM_DRIVEN_AGENT_ARCHITECTURE.md`：详细记录新旧架构对比、设计取舍、迁移计划

---

### 2026-06-04：五个 Claude Code 模式增强

**1. 对话历史压缩接入 LLM Agent 循环**
- 在 `llm_agent.py` 的工具结果注入前添加上下文压缩检查
- 估算当前对话 tokens，超过 75% 模型窗口时自动压缩
- 保留 system prompt + 原始用户消息 + 最近 8 条消息（4 个工具交互）
- 更早的消息替换为摘要行：`[Earlier context: N messages compacted. Continue naturally.]`
- 压缩事件记录到 AgentRun nodes

**2. 计划生成的自我纠正循环**
- 在 `_handle_chat_code_driven` 的计划生成流程中，`plan.generate` 之后插入反思步骤
- `_build_plan_reflection_prompt()`：要求 LLM 以该用户的身份审查生成的计划
- 检查：是否有加重伤病的动作、训练量与经验水平是否匹配、是否缺少热身/安全提示、是否要求用户没有的器材
- 如果发现问题，重新修改计划；如果安全，确认通过
- 反思事件记录到 AgentRun nodes

**3. 用户偏好学习（反馈闭环）**
- 复用之前创建的 `feedback_learner.py`（FeedbackCollector / PreferenceLearner / PromptEnhancer）
- 在 `_handle_chat_llm_agent` 中集成：每次 LLM Agent 运行前，从用户反馈历史学习偏好
- `_build_adaptive_prompt_wrapper`：包裹原始 system prompt builder，追加"Learned User Preferences"段落
- 最少 3 条负面反馈才生成模式；时间衰减（30 天半衰期）
- 反馈增强为 best-effort（失败不影响主流程）

**4. 工具结果引用提示**
- 更新 `llm_agent.py` 的 system prompt builder
- 在"Response guidelines"中添加指令：要求 LLM 在回复中自然引用工具结果
- 示例："Based on your current weight of 75kg..."、"Your active plan includes..."
- 这使用户能感知 Agent 确实查了资料，无需暴露技术细节

**5. 前端 Agent 过程卡片（已完成，记录于此）**
- ChatView.tsx 从 402 行重构，拆分为 ChatView + AgentProcessInline 组件
- 移除旧的 side panel（trace-panel、run-detail-panel、ThinkingProcess、TraceChips 全部删除）
- CSS 清理：移除 8000+ 字符的旧 trace-panel 样式，替换为 4700 字符的内联卡片样式
- TypeScript 编译通过

---

### 2026-06-04：CLAUDE.md 重构

- 完整重写 CLAUDE.md：中英双语架构概览 → 完整目录树 → 开发日志（本文档）
- 修正 LLM 提供商为 DeepSeek v4 Pro（主要），而非 Qwen
- 修正 Embedding 提供商为 Qwen text-embedding-v4

---

## 架构概览

### 双模式 Agent 架构

通过 `USE_LLM_DRIVEN_AGENT` 配置切换：

**模式 1：LLM 驱动（llm_agent.py）—— 主要模式**

System prompt 列出 13 个工具及 schema → LLM 迭代选择工具 → Host 执行并注入结果 → LLM 生成最终回复。上下文接近窗口上限时自动压缩早期消息。更多细节见开发日志"LLM 驱动 Agent 架构"章节。

**模式 2：代码驱动（_handle_chat_code_driven）—— 回退模式**

AgentPlanner 按关键词匹配意图，构建固定执行计划，按序执行 13 个工具。流式和非流式路径共用同一套管道。

### 安全护栏

`core/guardrails.py`：10 条规则，BLOCK/WARN/PASS 三级。上下文感知匹配（50 字符窗口）。总开销 < 1.5ms。

### 自纠正循环

计划生成后注入反思 prompt，LLM 角色扮演用户找出风险并修正。对标 Claude Code 的"生成代码→跑测试→修失败"模式。

### 用户偏好学习

60 天反馈窗口内聚合低评分（1-2 星），将投诉分类转化为行为指导注入 system prompt。最少 3 条负反馈才形成可靠模式，30 天半衰期衰减。

---

## API 端点

| Method | Path | Auth | 限流 | 用途 |
|--------|------|------|------|------|
| POST | `/v1/auth/register` | No | 5/min | 注册+获取 JWT |
| POST | `/v1/auth/login` | No | 5/min | 登录+获取 JWT |
| GET | `/v1/auth/me` | Yes | 30/min | 当前用户信息 |
| POST | `/v1/chat/sessions` | Yes | 30/min | 创建对话 |
| POST | `/v1/chat/messages` | Yes | 30/min | 发送消息（双模式调度） |
| POST | `/v1/chat/messages/stream` | Yes | 15/min | 流式消息（NDJSON） |
| POST | `/v1/profiles` | Yes | — | 更新档案 |
| POST | `/v1/checkins/daily` | Yes | — | 每日签到 |
| POST | `/v1/workouts/logs` | Yes | — | 训练日志 |
| POST | `/v1/plans/generate` | Yes | 10/min | 生成计划（含自反思） |
| POST | `/v1/plans/adjust` | Yes | — | 调整计划 |
| POST | `/v1/plans/review` | Yes | — | 周/月回顾 |
| GET | `/v1/users/{id}/dashboard` | Yes | — | 仪表盘 |
| GET | `/v1/agent-runs/{id}` | Yes | — | Agent 运行详情 |
| POST | `/v1/evals/run` | Yes | — | 评测 |
| POST | `/v1/feedback` | Yes | — | 提交反馈（upsert） |
| GET | `/v1/feedback` | Yes | — | 反馈列表（分页） |
| GET | `/v1/feedback/stats` | Yes | — | 30 天反馈统计 |
| POST | `/v1/nutrition/recognize` | Yes | — | 食物照片识别 |
| GET | `/v1/nutrition/logs` | Yes | — | 营养日志列表 |
| POST | `/v1/memory/items` | Yes | 30/min | 创建记忆 |
| GET | `/v1/memory/items` | Yes | — | 记忆列表 |
| GET | `/v1/memory/catalog` | Yes | — | 记忆目录 |
| POST | `/v1/memory/search` | Yes | 20/min | 搜索记忆 |
| POST | `/v1/agent/context` | Yes | 30/min | 构建上下文包 |
| POST | `/v1/agent/decision` | Yes | — | 记录 Agent 决策 |
| GET | `/v1/agent/decisions` | Yes | — | 决策列表 |
| GET | `/health` | No | 30/min | 健康检查 |
| GET | `/metrics` | No | — | Prometheus 指标 |

---

## 设计决策

- **双模式 Agent**：LLM 驱动为主（智能、自适应），代码驱动为兜底（确定、低成本）。共用同一组 13 个工具。通过 `.env` 中 `USE_LLM_DRIVEN_AGENT=true/false` 切换。
- **LLM 决策，Host 执行**：LLM 接收工具描述并决定调用哪些，Host 执行工具并注入结果。安全关键步骤（guardrail.check）以工具形式提供给 LLM。
- **类 Claude Code 上下文压缩**：对话接近窗口上限时，早期消息压缩为摘要。system prompt 和用户原始消息始终保留。
- **角色扮演式自我纠正**：生成计划后通过反思 prompt 让 LLM 扮演用户发现风险，弥补确定性规则的盲区。
- **反馈闭环**：用户评分（1-5 星）聚合 60 天数据，投诉分类转化为行为指导注入 system prompt。3+ 条负反馈才形成可靠模式。
- **安全是工具而非约束**：guardrail.check 以工具形式提供给 LLM 调用。即使 LLM 忘记调用，Host 也会在最终输出上运行护栏作为纵深防御。
- **工具结果可见化**：system prompt 要求 LLM 自然引用工具结果（如"根据你当前 75kg 的体重..."），让用户感知 Agent 的推理过程。

---

## 启动方式

### 后端
```bash
cd fast_api
pip install -r requirements.txt
docker-compose up -d    # PostgreSQL + pgvector
uvicorn fast_api.app.main:app --reload --port 8526
```

### 前端
```bash
cd web
npm install
npm run dev    # Vite 开发服务器，端口 5173
```

### 测试
```bash
python -m pytest tests/ -v
```

---

## 环境变量

主要 `.env` 变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | postgresql+psycopg://... | PostgreSQL 连接 |
| `LLM_PROVIDER` | deepseek | qwen / deepseek / openai / offline |
| `DEEPSEEK_API_KEY` | — | DeepSeek API 密钥（**主要使用**） |
| `DASHSCOPE_API_KEY` | — | Qwen API 密钥 |
| `OPENAI_API_KEY` | — | OpenAI API 密钥 |
| `EMBEDDING_PROVIDER` | qwen | qwen / openai / offline |
| `DEEPSEEK_CHAT_MODEL` | deepseek-v4-pro | 使用的模型 |
| `QWEN_EMBEDDING_MODEL` | text-embedding-v4 | Embedding 模型 |
| `USE_LLM_DRIVEN_AGENT` | false | 切换 LLM 驱动 / 代码驱动 |
| `USE_PGVECTOR` | true | 启用 pgvector |
| `JWT_SECRET_KEY` | change-me... | HS256 密钥（生产环境 64+ 字符） |
| `JWT_EXPIRE_MINUTES` | 1440 | Token 有效期（24h） |
| `CORS_ORIGINS` | localhost:5173 | 允许的来源 |
