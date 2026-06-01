# AI Fitness Coach — Recent Engineering Improvements

## 改进总览

最近五轮迭代覆盖了 LLM 应用从"能跑"到"生产可维护"的关键工程能力：安全护栏、Prompt 工程化、数据库迁移、可观测性、用户反馈闭环、语义缓存、以及自动化计划评审。

---

## 一、安全护栏 (Safety Guardrails)

### 做了什么

在 LLM 回复生成之后、返回给用户之前，插入一个**三层严重级别**的检查管道：

| 级别 | 行为 | 触发场景 |
|------|------|----------|
| BLOCK | 替换回复内容 | 医疗诊断、用药建议、极端热量限制 |
| WARN | 通过但记录标记 | 缺少热身提醒、补充剂建议 |
| PASS | 无动作 | 正常健身建议 |

共实现 **10 条规则函数**，每条规则独立判断并返回严重级别：

- `rule_medical_diagnosis` — 检测是否在诊断伤病（上下文感知，需要伤病术语和医疗动作同时出现）
- `rule_medication_advice` — 检测用药建议
- `rule_extreme_calorie_restriction` — 检测 <1200kcal 的极端饮食建议
- `rule_pain_continuation` — 检测"疼痛也要继续练"的建议
- `rule_warmup_omission` — 高强度训练缺少热身提醒
- `rule_supplement_claims` — 未经证实的补充剂功效声称
- `rule_missing_disclaimer` — 缺少"请咨询医生"免责声明
- `rule_exercise_modifier` — 检测"立即加重量""做到力竭"等危险修饰语
- `rule_eating_disorder` — 检测进食障碍相关模式
- `rule_excessive_exercise` — 检测过度训练信号

### 如何做的

核心设计是**上下文感知**的规则引擎，而非简单的关键词匹配。例如 `rule_medical_diagnosis` 需要伤病术语（"膝盖疼""shoulder injury"）和诊断动作（"可能是""it sounds like"）在**同一段 50 字符内**同时出现才触发，避免了"如果你膝盖疼应该去看医生"这样的安全表述被误伤。

```python
# 核心接口
def run_guardrails(text: str, user_message: str = "", profile=None) -> GuardrailResult:
    """
    运行所有规则，返回 GuardrailResult(action=BLOCK|WARN|PASS, flags=[...])
    如果任一规则返回 BLOCK，使用 BLOCK 模板替换回复内容
    """
```

性能基准：10 条规则全部运行 < 1.5ms，对比 LLM 调用延迟 1500ms，开销 < 0.1%。

### 集成位置

在三处管道中集成：
- `handle_chat_message()` — 同步聊天回复后
- `stream_chat_message()` — 流式回复后  
- `stream_chat_events()` — NDJSON 事件流中

每个返回结果都会在 `guardrail` 字段中携带检查结果，前端可据此展示安全提示。

### 面试可以说的点

1. **为什么需要安全护栏**：LLM 的输出是不可控的——即使 prompt 里说了"不要给医疗建议"，模型仍然可能给。护栏是 defense-in-depth 的最后一层。
2. **性能权衡**：1.5ms vs 1500ms，规则引擎几乎零开销，但你需要确保规则的复杂度可控。我们没有引入 regex-heavy 规则，每条规则最多 O(n) 扫描一次。
3. **上下文感知 vs 关键词**：纯关键词匹配会有大量误报。我们做了"近邻检查"——伤病词和诊断动作必须在 50 字符窗口内才触发，这是从 NLP 里借鉴的技术。
4. **三层严重级别**：BLOCK/WARN/PASS 的设计来自内容审核系统的实践。不是所有风险都要拦截——有些只需记录供后续分析。

---

## 二、Prompt 工程化与版本管理

### 做了什么

项目中原来有 **17 个硬编码的 Prompt 字符串**分散在 `coach_agent.py`、`model_provider.py`、`guardrails.py`、`eval_metrics.py`、`eval_service.py` 五个文件中。我们将它们全部提取到 **一个 YAML 文件** 中，并建立了**版本化的 Prompt Registry**。

### 如何做的

#### 1. 集中存储 (`fast_api/app/data/prompts.yaml`)

```yaml
# 每个 prompt 有版本、描述、最后修改时间、内容
coach_coaching_reply:
  version: "2.3"
  description: "Coaching reply prompt for general fitness Q&A"
  last_modified: "2026-05-20"
  content: |
    You are a professional fitness coach...
```

#### 2. Prompt Registry (`fast_api/app/core/prompts.py`)

```python
class PromptRegistry:
    """线程安全的、支持热重载的 Prompt 注册中心"""
    
    def __init__(self, path: str = "prompts.yaml"):
        self._prompts: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.load(path)
    
    def get(self, prompt_id: str) -> str:
        """获取 prompt 内容 — 高频调用，无锁读取"""
        return self._prompts[prompt_id]["content"]
    
    def version(self, prompt_id: str) -> str:
        return self._prompts[prompt_id]["version"]
    
    def reload(self) -> None:
        """热重载 — 不重启服务即可更新 Prompts"""
        with self._lock:
            self.load(self._path)
```

关键设计决策：
- **读写分离**：`get()` 是无锁的（只读已加载的 dict），`reload()` 才加锁。因为 99.9% 的操作是读取。
- **模块级单例**：`registry = PromptRegistry()` 在 import 时初始化，避免全项目传参。
- **版本追踪**：每个 prompt 有 version 字段，方便 A/B 测试和回滚。
- **模板支持**：`get("id", arg1=val1)` 支持 `.format()` 模板替换。

### 面试可以说的点

1. **为什么要做 Prompt 版本管理**：在 LLM 应用里，prompt 就是你的"业务逻辑"。改一个 prompt 可能比改代码的影响更大。版本化让你能追踪每次修改、做 A/B 测试、出问题时快速回滚。
2. **设计上的权衡**：我们没有用数据库存 prompts（会增加依赖和延迟），也没有用远程配置中心（过度设计）。YAML 文件 + 热重载是一个"刚好够用"的方案。
3. **线程安全**：使用 `threading.Lock` 而非 `asyncio.Lock`，因为这个 registry 在同步和异步代码中都可能被调用。
4. **实际收益**：从 5 个文件 17 处硬编码 → 1 个 YAML 文件 + 统一的 `registry.get("id")` 调用。非工程师（领域专家、产品经理）可以直接修改 YAML 文件来调整教练的人设和风格，不需要懂 Python。

---

## 三、Alembic 数据库迁移

### 做了什么

替换了原来 `database.py` 中 **65 行手写的 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 语句**，建立了标准的 Alembic 迁移框架。

### 如何做的

#### 1. 生成初始迁移 (`001_initial_schema.py`)

包含 **34 张表**的完整 DDL：users, user_profiles, body_metrics, conversation_sessions, chat_messages, training_plans, workout_logs, nutrition_logs, food_items, daily_checkins, recovery_logs, long_term_memories, memory_blocks, agent_runs, tool_calls, eval_cases, eval_runs, eval_results, coaching_cases 等。**13 个索引**，以及 pgvector 的 Vector 列定义。

#### 2. 增量迁移 (`002_feedback.py`, `003_semantic_cache.py`)

每次新增功能都创建独立的迁移文件，保持迁移历史的线性可追溯。

#### 3. 启动逻辑改造

```python
def init_db(retries=20, delay_seconds=1.5):
    """启动时自动运行 alembic upgrade head，失败则回退到 create_all"""
    try:
        command.upgrade(alembic_cfg, "head")  # 生产路径
    except Exception:
        Base.metadata.create_all(bind=engine)  # 开发/新环境回退
```

### 面试可以说的点

1. **为什么不用手写 ALTER TABLE**：手写 SQL 做版本管理有几个致命问题——不知道当前 schema 版本、无法回滚、多人协作冲突、没有历史记录。Alembic 解决了所有这些。
2. **迁移策略**：我们用了"渐进式迁移"——初始迁移包含全量 DDL（适合新部署），后续每次功能迭代追加增量迁移（适合已有数据的生产环境）。
3. **启动容错**：`alembic upgrade head` 失败时回退到 `create_all`。这在 Docker 首次启动或开发环境中非常有用——不需要预装 Alembic 迁移历史表。
4. **pgvector 集成**：迁移中使用了条件逻辑——如果 `use_pgvector=True` 用 Vector 列，否则用 JSONB 回退。这保证了项目在没有 pgvector 扩展的环境中也能运行。

---

## 四、Prometheus 可观测性

### 做了什么

从零构建了一个**零外部依赖**的 Prometheus 指标系统，并集成到 LLM 调用的每个关键路径中。

### 如何做的

#### 1. 指标原语 (`fast_api/app/core/metrics.py`)

```python
# 三个核心原语，各 30-60 行
@dataclass
class Counter:    # 单调递增计数器 — 请求数、错误数、token 数
    name, description, labelnames
    def inc(amount=1, **labels): ...
    def collect() -> list[str]: ...  # 输出 Prometheus 文本格式

@dataclass  
class Gauge:      # 可升降的值 — 活跃用户数、缓存条目数
    def set(value, **labels): ...
    def inc/dec(amount=1, **labels): ...

@dataclass
class Histogram:  # 分布统计 — 延迟、响应大小
    buckets: list[float]
    def observe(value, **labels): ...
    def collect() -> list[str]: ...
```

#### 2. 预定义指标（15 个）

| 分类 | 指标 | 类型 |
|------|------|------|
| LLM | `fitness_llm_requests_total{model,status}` | Counter |
| LLM | `fitness_llm_request_latency_seconds{model}` | Histogram |
| LLM | `fitness_llm_tokens_total{model,direction}` | Counter |
| Cache | `fitness_cache_hits_total`, `fitness_cache_misses_total` | Counter |
| Cache | `fitness_cache_entries` | Gauge |
| Guardrail | `fitness_guardrail_triggers_total{severity,rule_id}` | Counter |
| API | `fitness_api_requests_total{endpoint,method,status}` | Counter |
| API | `fitness_api_request_latency_seconds{endpoint}` | Histogram |
| Error | `fitness_errors_total{type}` | Counter |
| Agent | `fitness_agent_runs_total{run_type,status}` | Counter |
| Agent | `fitness_agent_run_latency_seconds{run_type}` | Histogram |
| Business | `fitness_active_users`, `fitness_plans_generated_total` | Gauge/Counter |

#### 3. 轻量级集成辅助函数

```python
def track_llm_call(model="unknown"):
    """工厂函数，返回 tracker 对象，支持 .success() 和 .failure()"""
    class Tracker:
        def success(self, tokens_in=0, tokens_out=0): ...
        def failure(self): ...
    return Tracker()
```

这是刻意设计的——不是 `with track_llm_call() as tracker:` 的上下文管理器模式，因为 LLM 调用在 async generator（流式）中，跨 `async for` 的上下文管理器很难写对。

#### 4. 集成到 coach_agent.py 的 5 个 LLM 调用点

```python
# 非流式
tracker = track_llm_call(model=self.model_provider.settings.chat_model)
try:
    reply = await self.model_provider.coach_reply(sys, usr)
    tracker.success()
except Exception:
    tracker.failure()

# 流式
tracker = track_llm_call(model=...)
try:
    async for chunk in self.model_provider.stream_coach_reply(sys, usr):
        yield chunk
    tracker.success()
except Exception:
    tracker.failure()
```

#### 5. `/metrics` 端点

```python
@app.get("/metrics")
def metrics(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        content=REGISTRY.generate_latest(), 
        media_type="text/plain; version=0.0.4"
    )
```

输出标准 Prometheus 文本格式，可直接被 Prometheus server scrape。

### 调试中修的一个 Bug

Histogram 的 `collect()` 方法最初有个 bug：`observe()` 已经做了累积（每个 value 增量更新所有 `le >= value` 的 bucket），但 `collect()` 又做了一次 `cum +=` 累积，导致 bucket 计数翻倍。修复方法是去掉 `collect()` 中的 `cum` 变量，直接输出 bucket 的计数值。

### 面试可以说的点

1. **为什么不引入 prometheus-client 库**：减少依赖。Prometheus 文本格式非常简单（HELP + TYPE + 指标行的 key-value 对），200 行代码就能实现核心功能。生产环境中如果已经在用 Prometheus operator，可以后续切换。
2. **指标设计原则**：RED 方法论（Rate, Errors, Duration）+ USE 方法论（Utilization, Saturation, Errors）。LLM 应用需要额外关注 Token 用量（成本指标）和安全护栏触发率（质量指标）。
3. **性能开销**：每个 Counter.inc() 调用 < 1μs（只是字典操作 + Lock）。Histogram.observe() 略高（需要遍历 buckets 列表），但我们默认 8 个 bucket，总耗时 < 5μs。
4. **跟踪 LLM 调用的设计考量**：在 async generator 中做 instrumentation 是个挑战——不能直接用 `with` 上下文管理器。我们选择了显式的 `tracker.success()` / `tracker.failure()` 模式，清晰但需要调用方记得调用。更好的方案是做一个 async context manager wrapper，但那需要侵入性地修改 `stream_coach_reply` 的返回类型。

---

## 五、用户反馈闭环

### 做了什么

建立了一个完整的反馈收集和分析系统：数据库模型 → API → 前端反馈 ID → 统计面板。

### 如何做的

#### 数据模型
```python
class UserFeedback:
    user_id, session_id, message_id  # 关联维度
    rating: int = 1-5                # 核心指标
    category: str | None              # "helpful", "incorrect", "too_generic"...
    comment: str | None               # 自由文本
    coach_reply_snapshot: str         # 回复快照（即使原消息被删除也能追溯）
```

#### API 设计
- `POST /v1/feedback` — 提交/更新反馈（upsert 模式，同一消息只能有一条反馈）
- `GET /v1/feedback/stats?days=30` — 统计面板：平均评分、评分分布、Top 分类
- `GET /v1/feedback?limit=20&offset=0&min_rating=3` — 分页列表

#### 集成到聊天管道
```python
# _save_message 现在返回 ChatMessage 对象
assistant_msg = self._save_message(session.id, user.id, "assistant", content)

# handle_chat_message 的返回 dict 包含 feedback_message_id
return {
    ...
    "feedback_message_id": assistant_msg.id,  # 前端用这个 ID 提交反馈
    ...
}
```

### 面试可以说的点

1. **反馈是 RLHF 的基础**：在 LLM 应用中，用户反馈是评估模型质量最重要的信号之一。它比自动化评估更接近真实用户体验。
2. **关闭循环**：目前我们收集反馈（数据积累阶段），下一步可以利用反馈数据做 prompt 优化（反馈驱动迭代）或 fine-tuning 数据集的构建。
3. **回复快照的设计**：`coach_reply_snapshot` 字段比较有意思——即使原始 ChatMessage 因为隐私合规被删除，我们仍然保留回复内容的快照用于质量分析。
4. **Upsert 模式**：每个 (user_id, message_id) 只能有一条反馈，重复提交会更新。这避免了用户重复点击导致的数据膨胀。

---

## 六、语义缓存

### 做了什么

实现了一个基于 pgvector 余弦相似度的 LLM 响应缓存。当用户提出语义相似的请求时，直接返回缓存的回复，跳过 LLM 调用。

### 如何做的

#### 缓存流程
```
用户请求 → 计算 (system_prompt + user_prompt) 的 embedding
         → pgvector 余弦相似度查询 (threshold=0.95)
         → 命中？返回缓存回复 + 更新 hit_count
         → 未命中？调用 LLM → 缓存结果 → 返回
```

#### 缓存键设计
- **`system_prompt_hash`**：用于快速过滤——只在与当前任务相同的缓存中搜索
- **`prompt_hash`**：精确匹配的键
- **`embedding`**：语义相似度搜索的核心，使用 pgvector 的 `cosine_distance` 函数

#### 相似度阈值
```python
DEFAULT_SIMILARITY_THRESHOLD = 0.95  # 非常高，只命中几乎相同的请求
DEFAULT_TTL_SECONDS = 86400          # 24 小时过期
MIN_PROMPT_LENGTH = 20               # 太短的请求不缓存
```

#### 集成点
在 `_coaching_reply()` 和 `_coaching_reply_stream()` 中：
```python
# 先查缓存
cached = self.cache.get(system_prompt, user_prompt)
if cached is not None:
    return cached  # 秒级响应，零 token 消耗

# 缓存未命中 -> 调用 LLM
reply = await self.model_provider.coach_reply(...)

# 缓存结果
self.cache.set(system_prompt, user_prompt, reply, model_name=...)
```

流式场景的处理：
```python
# 流式不能直接返回缓存字符串（需要 yield chunks）
cached = self.cache.get(system_prompt, user_prompt)
if cached is not None:
    async for chunk in self._stream_static_text(cached):
        yield chunk
    return

# 流式 LLM 调用，同时收集 chunks 以便缓存
chunks = []
async for chunk in self.model_provider.stream_coach_reply(...):
    chunks.append(str(chunk))
    yield chunk
self.cache.set(system_prompt, user_prompt, "".join(chunks))
```

### 面试可以说的点

1. **缓存粒度选择**：为什么缓存 input embedding 而不是 output embedding？因为基于 output 的缓存需要在生成 reply 后才知道它是否相似——这时候 LLM 已经调用了，缓存就没有意义了。基于 input 的缓存在调用前就能判断。
2. **相似度阈值 0.95 的选择**：健身教练场景中，类似的问题（"今天该练什么"）可能有完全不同的答案（取决于用户当天状态）。所以阈值设得很高（0.95），只缓存在输入几乎相同的情况下命中。如果你的场景是 FAQ 机器人，阈值可以降到 0.85-0.90。
3. **TTL 设计**：24 小时的 TTL 考虑了教练建议的时效性——一周前的训练建议可能不适用于当前状态。TTL 可以根据业务场景调整，甚至可以做成每个缓存条目独立的。
4. **成本收益**：对于高频的相似请求（如"今天吃什么"），缓存命中可以节省 LLM API 调用成本和 1-3 秒的延迟。对于低命中率的长尾请求，缓存查询本身的代价（数据库查询 + embedding 计算 < 50ms）是可接受的。
5. **pgvector 选型考量**：我们没有引入独立的向量数据库（如 Pinecone/Weaviate），因为 pgvector 已经能满足需求，且与现有 PostgreSQL 基础设施复用。IVFFlat 索引在缓存规模 < 10 万条时表现足够好。

---

## 七、自动化计划评审

### 做了什么

构建了一个定期（每周/每月）分析用户训练数据的服务，生成个性化的进度报告和改进建议。

### 如何做的

#### 数据聚合
从 5 个维度收集数据：
- **WorkoutLog** — 训练次数、时长、RPE（自觉疲劳度）、完成率
- **DailyCheckin** — 睡眠、精力、酸痛水平
- **BodyMetric** — 体重变化趋势
- **RecoveryLog** — 恢复评分
- **TrainingPlan** — 当前计划内容

#### 计算指标
```python
{
    "adherence_pct": workout_days / period_days * 100,  # 训练依从性
    "avg_rpe": ...,           # 平均训练强度
    "weight_change_kg": ...,  # 体重变化
    "avg_sleep_hours": ...,   # 平均睡眠
    "risk_signals": {         # 风险信号
        "high_soreness_days": ...,   # 高酸痛天数
        "low_energy_days": ...,      # 低精力天数
        "poor_sleep_days": ...,      # 睡眠不足天数
    }
}
```

#### 报告生成
- **有 LLM 时**：将统计数据发送给 LLM，生成个性化的、鼓励性的、有具体建议的评审报告（< 300 字）
- **无 LLM 时**：基于规则的模板生成，包含训练依从性描述、体重变化、恢复建议等

#### API
```python
POST /v1/plans/review?period_days=7   # 周报告
POST /v1/plans/review?period_days=30  # 月报告
```

### 面试可以说的点

1. **"规则 + LLM"的混合架构**：不是所有场景都需要 LLM。在 LLM 不可用时（API 故障、成本控制），规则引擎生成的基础报告仍然可用。这是 production LLM 应用的典型模式——LLM 提升质量上限，规则保证质量下限。
2. **风险信号的设计**：酸痛、精力、睡眠这三个指标是训练过度（overtraining）的早期信号。在健身领域，发现这些信号比告诉用户"你表现得很好"更有价值。
3. **可扩展的数据聚合**：`_fetch_workouts()`、`_fetch_checkins()` 等方法各自独立，数据源可以随时扩展（如接入 Apple Health、Strava），不需要改动评审逻辑。
4. **调度建议**：这个服务设计为可以被 scheduled task 定期调用（如每周一早上自动生成报告并推送给用户）。

---

## 面试高频问题和回答思路

### Q: 你们怎么保证 LLM 回复的安全性？

我们的安全护栏是一个三层规则引擎——BLOCK/WARN/PASS。关键设计是**上下文感知**而不是关键词匹配。比如"膝盖疼"这个词本身不触发拦截，只有和"可能是半月板损伤"这种诊断性表述在同一个 50 字符窗口内出现时才拦截。这样避免了把"如果膝盖疼，你应该去看医生"这种安全的建议误判。10 条规则总延迟 < 1.5ms，对用户体验几乎无影响。

### Q: Prompts 怎么管理？怎么知道改了 prompt 对效果的影响？

我们把 17 个 prompt 从 5 个代码文件中提取到统一的 YAML 注册中心。每个 prompt 有 version 字段，配合评估框架（eval framework）可以在改 prompt 后自动跑回归测试。注册中心支持热重载——改 YAML 文件后调用 `reload()` 即可生效，不用重启服务。

### Q: 数据库 schema 变更怎么管理？

全部通过 Alembic 迁移管理。初始迁移包含 34 张表的完整 DDL，后续每次迭代追加增量迁移。自动化回退到 `create_all` 的 fallback 逻辑保证了在 Docker 环境或 CI 中的可用性。pgvector 扩展使用条件逻辑——有就创建向量索引，没有就退化为 JSONB。

### Q: 怎么监控 LLM 应用的运行状态？

我们从零构建了 Prometheus 兼容的 metrics 系统（200 行代码，零外部依赖）。15 个预定义指标覆盖了 LLM 调用的延迟、token 用量、缓存命中率、安全护栏触发频率、API 请求量、和业务指标。`/metrics` 端点输出标准格式，可以直接被 Prometheus + Grafana 消费。

### Q: 怎么降低 LLM 调用成本？

做了两件事。一是语义缓存——用 pgvector 的余弦相似度在调用前检索是否有语义相似的缓存回复，相似度阈值 0.95（高精度场景）。二是"规则 + LLM"混合——像训练计划评审这种场景，LLM 能提升回复质量，但规则引擎保证在 LLM 不可用时仍然能给出有意义的基础报告。

### Q: 怎么收集用户反馈并利用它改进？

反馈系统收集 1-5 星评分 + 分类 + 自由文本评论。每个 LLM 回复的返回结果中携带 `feedback_message_id`，前端可以据此提交反馈。回复内容有快照——即使原消息被删除也保留。当前阶段是数据积累，下一步规划是用反馈数据做 prompt 优化和 fine-tuning 数据集构建。

### Q: 你在这个项目中学到了什么？

最大的收获来自三个方面。一是**工程化 Prompt**——把 prompt 当作代码一样版本化、测试、部署，这是 LLM 应用特有的工程挑战。二是**性能意识**——所有新增功能（护栏、缓存、指标）都进行了性能基准测试，确保不拖慢核心路径。三是**渐进式架构**——不为过度设计，规则引擎 10 条规则刚好够用，metrics 系统 200 行代码够用，缓存用 pgvector 而不是引入新的基础设施。做得刚好够用的设计比完美的设计更有价值。
