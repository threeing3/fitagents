# Codex Task: Introduce Hindsight-style Memory into fitagents

你现在要在当前项目中引入 Hindsight-style memory（事后回看式记忆机制），但不要直接接入外部 Hindsight Docker 或第三方 wrapper。当前阶段只做内部重构，保留现有 PostgreSQL + pgvector、MemoryCatalog、MemoryBlock、decision_rules、plan_templates、ContextBuilder 链路。

## 一、项目背景

当前项目是 AI Fitness Coach Agent，已有：
- FastAPI backend
- PostgreSQL + pgvector
- ContextBuilder
- MemoryManager
- MemoryCatalog
- MemoryBlock
- long_term_memories
- risk_notes
- workout/recovery/nutrition/symptom logs
- agent_decisions
- decision_rules
- plan_templates

当前记忆系统大致是：
IntentRouter -> MemoryCatalog -> FitnessRetrievalService -> ContextBuilder -> LLM

当前长期记忆检索已有：
- pgvector semantic recall
- importance / recency candidates
- simple keyword hit rerank

现在要升级为 Hindsight-style memory V1。

## 二、核心目标

实现 Hindsight 的三个核心思想：

1. retain（保留）
   把用户对话、训练日志、饮食日志、恢复记录、症状记录、Agent 决策，写成结构化长期记忆。

2. recall（回忆）
   在现有 search_memories 基础上，返回更清晰的记忆类型，并支持按 memory_network / fact_kind / entities / time range 过滤。

3. reflect（反思）
   从近期日志和 agent_decisions 中生成 observation memory（观察记忆）和 opinion memory（观点记忆），用于长期个性化教练判断。

## 三、不要做的事情

- 不要删除现有 long_term_memories。
- 不要删除 memory_catalog。
- 不要删除 memory_blocks。
- 不要删除 decision_rules。
- 不要删除 plan_templates。
- 不要引入外部 Hindsight Docker。
- 不要让 Hindsight 替代健康风险判断。
- 不要让 RAG 替代训练/饮食决策规则。
- 不要执行 docker compose down -v。
- 不要批量删除旧文件。
- 如果认为某个文件无用，必须先在日志中说明原因，不要直接删除。

## 四、需要先阅读的文件

请先阅读并总结这些文件，再开始修改：

- README.md
- MEMORY_SYSTEM.md
- fast_api/app/services/memory_system.py
- fast_api/app/services/context_builder.py
- fast_api/app/services/fitness_knowledge.py
- fast_api/app/db/models.py
- fast_api/app/api/memory_api.py
- tests/test_context_builder.py
- tests/ 中所有 memory/context 相关测试

阅读后，在日志中写入：
- 当前记忆系统入口
- 当前长期记忆写入流程
- 当前长期记忆检索流程
- 当前 ContextBuilder 如何使用记忆
- 当前 knowledge / rules / templates 的边界

## 五、数据库模型改造

在 LongTermMemory 模型中增加 Hindsight-style 字段。改动必须兼容旧数据。

建议字段：

memory_network: str
- world：世界事实记忆，保存用户事实和客观事件
- experience：经验记忆，保存 Agent 曾经做过的建议、决策、用户是否采纳
- observation：观察记忆，保存从多条事实中总结出的用户状态
- opinion：观点记忆，保存 Agent 对用户长期策略的判断

fact_kind: str
- user_profile_fact
- health_fact
- workout_event
- nutrition_event
- recovery_event
- symptom_event
- preference
- correction
- agent_action
- coach_observation
- coach_opinion
- daily_summary
- weekly_summary

occurred_start: datetime nullable
occurred_end: datetime nullable
mentioned_at: datetime nullable
entities: JSONB nullable
evidence: JSONB nullable
valid_from: datetime nullable
valid_until: datetime nullable

注意：
- 如果 models.py 中已有类似字段，不要重复创建。
- 如果已经有 confidence 字段，就复用，不要重复。
- 如果已有 memory_metadata 字段，保留它，但新增 entities/evidence 用于更明确的查询。
- migration 要写清楚默认值：旧数据默认 memory_network='world'，fact_kind 可根据 memory_type/category 推断，无法推断则设为 'user_profile_fact' 或 'unknown'。

## 六、MemoryManager 改造

在 fast_api/app/services/memory_system.py 中增加或改造以下能力：

### 1. create_memory_item 兼容 Hindsight 字段

create_memory_item 需要支持：
- memory_network
- fact_kind
- occurred_start
- occurred_end
- mentioned_at
- entities
- evidence
- valid_from
- valid_until

embedding 仍然保留，embedding 文本建议包含：
- memory_network
- fact_kind
- category
- summary
- content
- entities

### 2. 增加 retain_memory 方法

新增方法：

retain_memory(
    user_id,
    content,
    memory_network,
    fact_kind,
    category=None,
    summary=None,
    entities=None,
    evidence=None,
    occurred_start=None,
    occurred_end=None,
    importance_score=0.6,
    confidence_score=0.75,
    source_type='system'
)

作用：
- 统一写入 Hindsight-style memory
- 自动生成 summary
- 自动补 entities
- 自动更新 memory_catalog
- 自动更新 memory_blocks

### 3. 增加轻量实体抽取

不要调用复杂外部服务。先做规则版实体抽取。

支持以下实体：
- exercise：卧推、深蹲、硬拉、引体、bench、squat、deadlift、pull-up
- symptom：胸闷、头晕、疼痛、刺痛、麻木、呼吸困难、酸痛
- condition：甲亢、甲状腺
- medication：赛治、甲巯咪唑、methimazole
- nutrition：蛋白粉、鱼油、香蕉、外卖、海鲜
- recovery：睡眠、疲劳、压力、心率
- goal：增肌、减脂、力量、恢复

输出格式：
[
  {"type": "symptom", "name": "胸闷", "canonical": "chest_tightness"},
  {"type": "medication", "name": "赛治", "canonical": "methimazole"}
]

### 4. 增加 agent_decision -> experience memory

新增方法：

retain_agent_decision_as_experience(user_id, decision)

把 agent_decisions 中的重要决策转成 experience memory。

示例：
memory_network='experience'
fact_kind='agent_action'
category='decision'
content='Agent advised deload because sleep was poor and pain risk was present...'
evidence=[{"table": "agent_decisions", "id": "..."}]

## 七、Recall 改造

当前 search_memories 不要删，改造成兼容旧调用。

新增参数：
- memory_network: str | None
- fact_kind: str | None
- entities: list[str] | None
- occurred_after: datetime | None
- occurred_before: datetime | None
- include_expired: bool = False

检索流程 V1：

1. semantic_candidates
   使用现有 pgvector cosine_distance。

2. important_recent_candidates
   使用 importance + created_at。

3. keyword_candidates
   先用轻量关键词匹配，不要急着引入 Elasticsearch。
   如果项目已有 PostgreSQL FTS 能力就用 FTS，否则先用 ilike 或 tokens 命中。

4. entity_candidates
   如果 query 里抽取到实体，则优先召回 entities 命中的记忆。

5. merge + rerank
   先不要上复杂 cross-encoder。
   使用简单 RRF-style ranking：
   - semantic rank
   - keyword rank
   - entity rank
   - importance
   - recency
   - risk priority

需要保证：
- risk / health / medication 相关记忆优先级更高。
- expired 记忆默认不返回。
- memory_network='opinion' 的记忆不能当作事实使用，要在返回 payload 中标清楚。

## 八、Reflect 改造

新增 ReflectionService：

文件建议：
fast_api/app/services/reflection_service.py

功能：

### 1. reflect_user_memory(user_id)

读取：
- recent workout logs
- recent nutrition summaries
- recent recovery logs
- recent symptom logs
- active risk notes
- recent agent_decisions
- relevant long_term_memories

生成：
- observation memory
- opinion memory

### 2. observation memory 示例

memory_network='observation'
fact_kind='coach_observation'
category='recovery'
content='用户最近一周睡眠和疲劳波动较大，训练进阶应保守。'
confidence=0.75

### 3. opinion memory 示例

memory_network='opinion'
fact_kind='coach_opinion'
category='training'
content='基于近期恢复和风险记录，Agent 判断用户当前更适合保守进阶，而不是频繁冲大重量。'
confidence=0.8
evidence=[...]

### 4. 反思必须可追踪

每条 observation/opinion memory 必须带 evidence，说明来源于哪些日志或 agent_decisions。

## 九、API 改造

在 memory_api.py 中新增或扩展：

1. POST /v1/memory/retain
   手动写入 Hindsight-style memory。

2. POST /v1/memory/reflect
   对某个 user_id 执行一次 reflect_user_memory。

3. POST /v1/memory/search
   保持旧接口兼容，但支持新过滤条件：
   - memory_network
   - fact_kind
   - entities
   - occurred_after
   - occurred_before

4. GET /v1/memory/catalog
   保持兼容。

所有新增接口必须有 Pydantic schema。

## 十、ContextBuilder 改造

ContextBuilder 构建上下文时，需要把 relevant_memories 分成：

- world_memories
- experience_memories
- observation_memories
- opinion_memories

但为了兼容旧 prompt，也可以继续保留 relevant_memories。

注意：
- world_memories 可作为事实使用。
- experience_memories 用来参考过去建议。
- observation_memories 用来理解用户当前模式。
- opinion_memories 只能作为 Agent 判断参考，不能当作客观事实。
- active_risk_notes 和安全规则仍然优先于 opinion_memories。

## 十一、知识库/规则/模板边界不能破坏

必须保留：

- explanation_knowledge 和 coaching_cases 走 RAG。
- decision_rules 和 plan_templates 走结构化匹配。
- 训练/饮食/风险决策不允许只靠 Hindsight recall。
- 如果 Hindsight recall 与 active_risk_notes 或 decision_rules 冲突，后者优先。

## 十二、测试要求

至少新增或更新测试：

1. test_retain_hindsight_memory
   验证 retain_memory 能写入 memory_network / fact_kind / entities / evidence。

2. test_search_memory_by_network
   验证 search_memories 可以按 memory_network 过滤。

3. test_search_memory_entity_priority
   验证 query 命中“甲亢/赛治/胸闷”等实体时，risk/health memory 排名靠前。

4. test_reflection_creates_observation_and_opinion
   验证 reflect_user_memory 会生成 observation 和 opinion。

5. test_context_builder_groups_hindsight_memories
   验证 ContextBuilder 输出 world/experience/observation/opinion 分组。

6. test_rules_override_opinion_memory
   验证风险规则优先于 opinion memory。

## 十三、日志要求

把实现过程记录到 logs/experiments/hindsight-memory-v1-*.md，包括：
- 修改动机
- 修改文件
- 数据库字段变化
- 新增方法
- 测试命令
- 测试结果
- 已知问题
- 下一步建议

## 十四、验收命令

完成后运行：

python -m compileall fast_api\app tests

python -m pytest tests

如果项目已有 smoke-test：

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke-test.ps1

如果测试失败：
- 不要隐藏失败。
- 在日志中记录失败原因。
- 修复后重新运行。
- 最终给出完整变更摘要。

## 十五、最终输出

完成后请输出：

1. 实现摘要
2. 修改文件列表
3. 新增数据库字段
4. 新增 API
5. 新增/修改测试
6. 运行过的命令和结果
7. 是否影响旧接口
8. 后续可继续做 BM25 / PostgreSQL FTS / RRF / memory graph 的建议

