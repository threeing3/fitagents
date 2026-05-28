# AI Fitness Coach Agent

面向普通健身用户的 AI 私教 Agent 项目。当前版本已经从原始一次性计划 Demo 瘦身为主线清晰的 Web 产品原型：用户通过对话建档，Agent 将身体数据、训练目标、器械条件、饮食习惯、健康边界和每日反馈写入 PostgreSQL，并基于长期记忆、结构化规则和训练模板生成或调整建议。

## 当前能力

- 对话式建档：抽取年龄、性别、身高、体重、目标、训练经验、训练频率和器械条件。
- 长期记忆：记录健康/用药背景、饮食习惯、训练表现、纠错信息和近期状态。
- 健身知识系统：区分解释知识、结构化决策规则、训练/饮食模板和教练案例。
- ContextBuilder：按用户意图组合用户档案、长期记忆、知识召回、规则和模板。
- 动态计划：生成训练与营养目标，并根据疲劳、睡眠、酸痛和完成度调整训练量。
- 可观测日志：每次 Agent run 写入可读日志，记录节点、召回、规则命中、模板选择和耗时。
- Eval harness：覆盖建档、纠错、疲劳调整、饮食外食、知识召回和安全边界。
- Web UI：React/Vite 页面支持流式对话和逐字显示。

## 技术栈

- Backend: FastAPI
- Database: PostgreSQL + pgvector
- Agent runtime: service-orchestrated single-agent workflow
- Model provider: DeepSeek by default, with Qwen/OpenAI switches and offline fallback
- Frontend: React + Vite
- Tests: pytest + smoke test + eval logs

## 目录结构

```text
.
├── docker-compose.yml
├── .env.example
├── fast_api/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── api/
│       │   ├── coach_platform.py
│       │   └── memory_api.py
│       ├── core/
│       ├── data/fitness_knowledge/
│       ├── db/
│       ├── schemas/
│       └── services/
├── web/
├── scripts/
│   ├── start-dev.ps1
│   ├── smoke-test.ps1
│   └── repair-current-demo-profile.ps1
├── tests/
└── logs/
```

## 环境变量

首次运行时复制模板：

```powershell
Copy-Item .env.example .env
```

默认配置使用 DeepSeek：

```env
LLM_PROVIDER=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-v4-pro
DEEPSEEK_API_KEY=你的 DeepSeek API Key
```

如需切换 Qwen/DashScope：

```env
LLM_PROVIDER=qwen
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_CHAT_MODEL=qwen-plus
DASHSCOPE_API_KEY=你的 DashScope API Key
```

Embedding 可以独立配置。开发演示时如果外部 embedding 网络不稳定，可使用：

```env
EMBEDDING_PROVIDER=offline
USE_PGVECTOR=true
```

## Docker Compose 运行

```powershell
cd "C:\Users\Lenovo\Documents\New project 4\ai-fitness-planner"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

启动成功后：

- Web UI: http://localhost:5173
- API docs: http://localhost:1015/docs
- Health check: http://localhost:1015/health
- PostgreSQL: localhost:4553

停止服务：

```powershell
docker compose down
```

仅在确认不需要历史数据时清空数据库卷：

```powershell
docker compose down -v
```

## PyCharm 开发

推荐用 Docker Compose 跑 PostgreSQL，PyCharm 调试 FastAPI：

```powershell
docker compose up -d postgres
.\.venv\Scripts\python.exe -m uvicorn fast_api.app.main:app --reload --port 1015
```

如果需要重建本地虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r fast_api\requirements.txt
```

前端依赖已不提交到仓库。需要本机前端开发时：

```powershell
cd web
npm install
npm run dev
```

如果本机 Node 版本过旧，优先使用 Docker Compose 中的前端服务。

## 常用命令

静态检查：

```powershell
.\.venv\Scripts\python.exe -m compileall fast_api\app tests
```

单元测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests
```

API smoke test：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke-test.ps1
```

Compose 配置检查：

```powershell
docker compose config --quiet
```

## 主要 API

- `POST /v1/chat/sessions`：创建用户专属 Agent 会话
- `POST /v1/chat/messages/stream`：发送消息并流式返回教练回复
- `POST /v1/checkins/daily`：记录每日睡眠、疲劳、酸痛、饮食执行和训练完成度
- `POST /v1/workouts/logs`：记录动作、重量、次数、组数、RPE 和备注
- `POST /v1/plans/generate`：生成训练与营养计划
- `POST /v1/plans/adjust`：基于反馈调整计划
- `GET /v1/users/{user_id}/dashboard`：读取今日计划、档案、记忆和关键指标
- `GET /v1/agent-runs/{run_id}`：查看一次 Agent 执行 trace
- `POST /v1/evals/run`：运行 eval harness

## 数据与日志

PostgreSQL 统一保存：

- 用户、档案、训练计划、训练日志、饮食日志、每日 check-in
- 长期记忆、会话消息、agent run、tool call
- prompt/eval 数据和知识库数据
- pgvector embedding

日志目录：

- `logs/agent-runs/`：每次对话的可读运行日志
- `logs/experiments/`：启动、smoke test 和 eval 日志

日志不会记录 API key。

## 面试展示重点

- 不是一次性 prompt demo，而是有用户档案、长期记忆、规则、模板、反馈和评估闭环的 Agent 产品。
- RAG 只用于解释知识和教练案例，影响训练/饮食决策的内容使用结构化 `decision_rules` 与 `plan_templates`。
- Agent 可观测性完整：能看见意图识别、profile extraction、memory writes、retrieval、rule match、template selection、LLM/fallback 和 latency。
- 支持 DeepSeek/Qwen/OpenAI provider abstraction，模型失败时有可演示的 deterministic fallback。
- 保持 Web-first 主线，旧 Streamlit、MongoDB、FAISS、USDA demo 路径已移除。

## 健康边界

本项目提供健身建议，不做医疗诊断或用药建议。疼痛、伤病、疾病、极端节食、胸闷、头晕、心悸等场景需要触发安全提示，并建议咨询专业人士。
