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

### Agent Lab（智能体实验室）

登录后进入 Agent Lab，可回放当前用户最近的 AgentRun（智能体执行记录）。页面按“理解请求 → 制定计划 → 召回上下文 → 执行工具 → 验证结果 → 安全检查 → 生成回复”展示脱敏决策轨迹，并自动标记规划降级、工具失败、验证器问题、安全护栏介入和检索降级。接口不会返回原始用户输入、工具参数、模型上下文或本地日志路径。

实验室同时公开 `agent_challenge_v1` 高难度诊断基线：120 条固定测试样例覆盖多意图、安全绕过、记忆冲突、指代不明、参数缺失和口语噪声。该集合固定标记为 `partition=test`、`training_eligible=false`，不得进入训练数据。

日志不会记录 API key。

## 面试展示重点

- 不是一次性 prompt demo，而是有用户档案、长期记忆、规则、模板、反馈和评估闭环的 Agent 产品。
- RAG 只用于解释知识和教练案例，影响训练/饮食决策的内容使用结构化 `decision_rules` 与 `plan_templates`。
- Agent 可观测性完整：能看见意图识别、profile extraction、memory writes、retrieval、rule match、template selection、LLM/fallback 和 latency。
- 支持 DeepSeek/Qwen/OpenAI provider abstraction，模型失败时有可演示的 deterministic fallback。
- 保持 Web-first 主线，旧 Streamlit、MongoDB、FAISS、USDA demo 路径已移除。

## 健康边界

本项目提供健身建议，不做医疗诊断或用药建议。疼痛、伤病、疾病、极端节食、胸闷、头晕、心悸等场景需要触发安全提示，并建议咨询专业人士。

## 算法实验层（面试项目）

项目新增 `algorithm/` 离线实验层，用于展示大模型应用算法和业务算法后训练能力。它不改变现有 FastAPI 主链路，主要负责：

- 从 Agent run、tool call、反馈和决策结果导出脱敏训练样本。
- 构建 SFT、工具决策、安全和偏好数据集。
- 评估意图识别、记忆召回、工具规划、回复重排序和业务接受率。
- 在 AutoDL 上使用独立训练依赖运行 QLoRA/DPO。
- 保存数据 manifest、实验配置、评测报告和模型卡。

### 阶段三可信基线（2026-08-09）

- 38 条固定业务样例全部通过；固定评测集包含 120 条意图、80 条召回、200 条工具规划、150 条安全和 100 条回复质量样例。
- 意图 Macro-F1 为 1.00，风险 Recall 为 1.00；这些结果来自 `seed_eval` 规则覆盖集，只证明固定场景门禁，不代表真实线上分布。
- BM25 的 Recall@5 为 0.95；当前没有带真实向量服务来源的分数，因此向量结果明确显示 `vector unavailable`，不会用 SHA-256 伪向量替代。
- 规则 Planner 的工具选择和顺序准确率均为 1.00，结构合法率为 1.00；未配置模型时不伪造 LLM Planner 结果。
- 训练工厂可复现生成 1200 条 `synthetic` 样本，按 50 个用户整体切分为 960/120/120，零用户泄漏；业务结果只标记为 `simulated_outcome`。
- 当前没有经过真实人工审核的偏好对，因此 DPO 保持关闭。完整脱敏结果见 `algorithm/evaluation/reports/maturity_03_baseline.summary.json` 和 `algorithm/datasets/manifests/maturity_03_synthetic.summary.json`。

### Intent 04 真实 Qwen3-4B 后训练（2026-08-18）

- 在单张 RTX 4090 上完成 Qwen3-4B 的 4-bit QLoRA 意图适配器训练，并通过独立进程重载。
- 120 条永久隔离挑战集上，适配器结构合法率为 100%，风险召回率为 100%；安全合并后完全匹配率由底座的 5.83% 提升到 13.33%。
- 完全匹配率仍低，因此当前只标记为 `verified_offline`，不声明线上业务提升；生产安全仍由确定性规则控制。
- 脱敏发布摘要见 `algorithm/evaluation/reports/intent_qwen3_4b_release.summary.json`，完整训练说明见 `docs/QWEN3_INTENT_TRAINING.md`。

统一阶段三门禁：

```powershell
python -m algorithm.evaluation.build_fixed_evals --verify
python -m algorithm.evaluation.run_maturity_gate `
  --experiment-id maturity_03_algorithms_20260809 `
  --output <new-experiment-report.json>
```

项目还提供“学习模式”，用于把每个算法模块变成可练习、可验收、可面试表达的课程。默认采用 conversation-first（对话优先）方式：你在 Codex 对话中回答概念题和实验预测，由 Codex 执行命令、展示结果、维护进度和实验日志，你不需要自己操作终端。

```powershell
python -m algorithm.learning.mode list
python -m algorithm.learning.mode next
python -m algorithm.learning.mode show 03_intent_and_routing
python -m algorithm.learning.mode check 03_intent_and_routing
python -m algorithm.learning.mode progress
```

学习方法和 4–6 周能力地图见 [`docs/LEARNING_MODE.md`](docs/LEARNING_MODE.md)。
学习总控协议见 [`docs/LEARNING_CONTROL_PROTOCOL.md`](docs/LEARNING_CONTROL_PROTOCOL.md)，机器可读配置见 [`algorithm/research_state/learning_control.json`](algorithm/research_state/learning_control.json)。

推荐先阅读：

- `docs/ALGORITHM_UPGRADE_PLAN.md`
- `docs/DATA_GOVERNANCE.md`
- `docs/EVALUATION_PROTOCOL.md`
- `docs/MODEL_CARD.md`
- `docs/INTERVIEW_DEMO_SCRIPT.md`
- `docs/CI_AND_DEPLOYMENT.md`
- `docs/PRODUCT_SECURITY.md`

最小数据管线：

```powershell
python -m algorithm.data.export_traces --output algorithm/datasets/manifests/training_examples.jsonl --db local_dev.db --log-dir logs/agent-runs --salt "local-dev-salt"
python -m algorithm.data.validate_dataset algorithm/datasets/manifests/training_examples.jsonl
python -m algorithm.datasets.build_sft_dataset algorithm/datasets/manifests/training_examples.jsonl algorithm/datasets/manifests/sft_train.jsonl
python -m algorithm.datasets.build_preference_dataset algorithm/datasets/manifests/training_examples.jsonl algorithm/datasets/manifests/preference_pairs.jsonl
python -m algorithm.app_algorithms.intent_baseline tests/evals/intent_eval_cases.json
```

需要在真实轨迹不足时做学习实验，可使用一键数据集总工厂；合成样本会显式标记来源，不会伪装成真实业务数据：

```powershell
python -m algorithm.datasets.build_bundle --input algorithm/datasets/manifests/training_examples.jsonl --output-dir <ignored-experiment-directory> --synthetic-count 1200 --seed 42
python -m algorithm.business.business_baseline --count 240 --seed 42 --experiment-id business-baseline-v1 --output <report.json>
python -m algorithm.app_algorithms.memory_retrieval_eval
```

AutoDL 训练入口：

```powershell
pip install -r algorithm/training/requirements-training.txt
python -m algorithm.training.sft.train_qlora --config algorithm/training/configs/sft_qwen3b.json --dry-run
python -m algorithm.training.sft.train_qlora --config algorithm/training/configs/sft_qwen3b.json
python -m algorithm.training.dpo.train_dpo --config algorithm/training/configs/dpo_qwen3b.json --dry-run
python -m algorithm.training.dpo.train_dpo --config algorithm/training/configs/dpo_qwen3b.json
```

只有在 `preference_pairs.jsonl` 含有经过审核的 `chosen/rejected` 对后才运行 DPO；数据不足时保持空文件，不从未标注回复推断偏好。
因此，真实数据导出的 DPO dry-run 失败且提示数据集为空是预期的安全门禁；完成合成/专家偏好数据审核后，再把配置中的 `dataset_path` 指向非空文件。
