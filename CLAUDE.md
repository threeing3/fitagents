# CLAUDE.md — AI Fitness Coach Agent

## Project overview

A full-stack AI private fitness coach platform. Users chat with a coach agent that retrieves relevant knowledge, applies decision rules, selects training plan templates, and matches coaching cases — all backed by long-term memory, RAG embeddings, and multi-provider LLM support (Qwen / DeepSeek / OpenAI).

The system is bilingual (Chinese + English) and designed for users ranging from complete beginners to advanced lifters, with safety guardrails for medical conditions, injury recovery, female cycle adjustments, and overtraining detection.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend framework | FastAPI 0.115 |
| Database | PostgreSQL + pgvector (dev), SQLite in-memory (test) |
| ORM | SQLAlchemy 2.0 declarative |
| LLM providers | Qwen (DashScope), DeepSeek, OpenAI (via langchain-openai) |
| Embeddings | Qwen text-embedding-v4, OpenAI text-embedding-3-small, offline hash fallback |
| Auth | JWT (python-jose + passlib/bcrypt), HS256 |
| Rate limiting | slowapi 0.1.9 |
| Observability | LangSmith tracing, custom AgentRunLogger |
| Frontend | React 18 + TypeScript, Vite, Tailwind CSS, Lucide icons |
| CI/CD | GitHub Actions (lint, type-check, test matrix, Docker build, OpenAPI validation) |
| Container | Docker + docker-compose (PostgreSQL 16 + pgvector) |

## Directory structure

```
ai-fitness-planner/
├── .github/workflows/
│   ├── ci.yml              # Lint, type-check, test matrix, Docker build, OpenAPI validation
│   └── deploy.yml          # Docker push + staging/production deploy stubs
├── docker-compose.yml      # PostgreSQL 16 + pgvector service
├── fast_api/
│   ├── requirements.txt
│   └── app/
│       ├── main.py         # FastAPI app, CORS, startup, limiter
│       ├── api/
│       │   ├── auth_api.py       # POST /v1/auth/register, /login, GET /me
│       │   ├── coach_platform.py # Chat, profiles, checkins, workouts, plans, dashboard
│       │   └── memory_api.py     # Memory CRUD, catalog, search, context, decisions
│       ├── core/
│       │   ├── auth.py           # JWT creation, get_current_user dependency
│       │   ├── config.py         # Pydantic Settings (env-driven)
│       │   ├── errors.py         # Custom exceptions + centralized FastAPI handlers
│       │   ├── retry.py          # Exponential backoff decorator + async helper
│       │   └── security.py       # Password hashing (passlib/bcrypt)
│       ├── db/
│       │   ├── database.py       # Engine, SessionLocal, Base, get_db, init_db
│       │   └── models.py         # SQLAlchemy models (User, UserProfile, ChatMessage, etc.)
│       ├── schemas/
│       │   └── agent.py          # Pydantic request/response schemas
│       ├── services/
│       │   ├── coach_agent.py         # Main orchestrator — chat, onboarding, plan gen
│       │   ├── context_builder.py     # IntentRouter + ContextBuilder (RAG context packets)
│       │   ├── decision_rules.py      # TrainingDecisionRules (progression, deload, safety)
│       │   ├── decision_logger.py     # Persists agent decisions for audit
│       │   ├── fitness_knowledge.py   # JSON-based RAG: rules, templates, knowledge, cases
│       │   ├── fitness_math.py        # Macro targets, volume adjustment formulas
│       │   ├── memory_system.py       # MemoryManager (CRUD, search, catalog, blocks)
│       │   ├── model_provider.py      # LLM/embedding abstraction + retry
│       │   └── agent_observability.py # AgentRunLogger (structured node/event tracing)
│       └── data/fitness_knowledge/
│           ├── decision_rules.json        # 25 rules (injury, plateau, cycle, overtraining, etc.)
│           ├── plan_templates.json        # 19 templates (goal × level × equipment matrix)
│           ├── explanation_knowledge.json # 30 knowledge items (sports science fundamentals)
│           ├── coaching_cases.json        # 21 coaching scenarios
│           └── eval_cases.json            # 31 eval cases with machine-checkable expectations
├── tests/
│   ├── conftest.py
│   ├── test_agent_observability.py  # AgentRunLogger tests
│   ├── test_api_integration.py      # 21 FastAPI TestClient integration tests
│   ├── test_context_builder.py      # 18 intent classification + context building tests
│   ├── test_decision_rules.py       # 24 rule matching + TrainingDecisionRules tests
│   ├── test_eval_cases.py           # 7 eval harness tests (classification + execution)
│   ├── test_fitness_knowledge.py    # 26 knowledge retrieval + template selection tests
│   ├── test_fitness_math.py         # Macro calculation + adjustment tests
│   ├── test_memory_rules.py         # Memory extraction rule tests
│   └── test_memory_system.py        # MemoryManager CRUD tests
└── web/
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── main.tsx           # App entry with AuthProvider gating
        ├── api.ts             # API client with auth header injection
        ├── AuthContext.tsx     # Global auth state + token persistence
        ├── LoginView.tsx       # Register/login form
        ├── ChatView.tsx        # Chat interface with streaming
        ├── DashboardView.tsx   # User stats, today's plan, suggestions
        ├── CheckinView.tsx     # Daily check-in form
        ├── types.ts            # Shared TypeScript types
        └── styles.css          # Tailwind + dark theme
```

## Architecture

### Agent pipeline

Each chat message flows through a multi-stage pipeline orchestrated by `CoachAgentService`:

```
User message
  → ProfileExtractorAgent (rule + optional LLM extraction)
  → MemoryAgent (long-term memory write)
  → IntentRouter (keyword-based classification into 8 intents)
  → [if onboarding incomplete → onboarding reply]
  → [if safety trigger → static safety reply]
  → ContextBuilder (builds context packet: profile + memories + risks + knowledge)
  → FitnessKnowledgeService (RAG retrieval: rules + templates + knowledge + cases)
  → CoachLLM (final reply with all context, retried with exponential backoff)
  → Persist (message, agent run log, tool calls)
```

### Intent classification

`IntentRouter.classify()` uses keyword matching to classify messages into 8 intents:
`progression_decision`, `injury_or_risk`, `nutrition_advice`, `training_log`, `recovery_check`, `weekly_review`, `general_chat`, `onboarding`.

Safety takes priority: messages containing high-risk medical terms are always classified as `injury_or_risk`.

### Knowledge system

The `FitnessKnowledgeService` loads five JSON files into memory on startup and seeds them into PostgreSQL. Retrieval works without live embeddings (keyword + condition matching):

- **Decision rules** (25): Structured `condition_json` with `all`/`any`/`path`/`op`/`value` matching. Each rule has an `action_json` (decision + reason). Priority-sorted, safety-aware.
- **Plan templates** (19): Matched by goal × level × equipment_available × days_per_week. Returns the closest template.
- **Explanation knowledge** (30): Keyword + intent + tag matching. Provides sports science context.
- **Coaching cases** (21): Scenario matching for empathetic, example-driven coaching.
- **Eval cases** (31): Machine-checkable test scenarios covering all rule/template/knowledge/case combinations.

### Retry with backoff

`core/retry.py` provides:
- `retry_with_backoff` decorator (async + sync)
- `async_retry_call` helper for one-off calls
- Auto-detection of retryable errors: TimeoutError, ConnectionError, 429/5xx HTTP codes, keyword matching on error messages
- Configurable exponential backoff with random jitter (±50%)

Applied to `coach_reply()` (decorator) and `stream_coach_reply()` (inline retry with first-chunk health check).

### Error handling

`core/errors.py` defines custom exceptions (`LLMTimeoutError`, `LLMServiceUnavailableError`, `DatabaseError`, `AuthenticationError`, `AuthorizationError`, etc.) and registers centralized FastAPI handlers that return consistent JSON:

```json
{"error": {"code": "llm_timeout", "status": 504, "message": "..."}}
```

### Rate limiting

Per-endpoint limits via slowapi:
- Auth: register/login 5/min, me 30/min
- Chat: send 30/min, stream 15/min
- Plans: generate 10/min
- Memory: create 30/min, search 20/min, context 30/min
- Default: 60/min

## Key improvements made

### Phase 3 — Knowledge base enrichment (18 → 126 items)
- **decision_rules.json**: 4 → 25 rules. Added injury recovery, plateau-breaking, menstrual cycle phases, overtraining detection, joint pain, chronic sleep, high stress, nutrition timing, beginner form priority, scheduled deload, warmup, age 45+, hydration/heat, active rest.
- **plan_templates.json**: 3 → 19 templates. Full matrix of goal (muscle_gain/fat_loss/general_fitness/strength/maintenance) × level (beginner/intermediate/advanced) × equipment (gym/home_dumbbell/bodyweight/resistance_bands). Added nutrition templates (vegetarian, clean bulk, balanced maintenance).
- **explanation_knowledge.json**: 3 → 30 items. Covered macronutrients, training volume, RPE, sleep science, deload science, hypertrophy mechanisms, rep ranges, rest periods, energy balance, metabolic adaptation, cardio types, supplements, mobility, mind-muscle connection, periodization, warmup, hydration, female training cycle, age-related training, beginner adaptation, injury return, core training.
- **coaching_cases.json**: 3 → 21 cases. Added beginner DOMS, plateau strength, injury return, gym intimidation, inconsistent schedule, supplement questions, wedding prep, home limited equipment, travel training, meal timing obsession, comparison discouragement, winter motivation, advanced plateau, older adult starting, female luteal phase, weight loss fear of bulking, vegetarian muscle gain, knee pain squat replacement.
- **eval_cases.json**: 5 → 31 cases. Each case has machine-checkable expectations: `must_include_knowledge`, `must_trigger_rule`, `must_include_template`, `must_include_case`.

### Phase 4 — Test coverage (24 → 109 tests, 8 → 9 files)
- **test_context_builder.py**: 3 → 18 tests. IntentRouter classification of all 8 intents, priority ordering (risk > progression > training_log), pain/nutrition/recovery variation recognition, Chinese/English exercise extraction, general chat minimal context, nutrition intent filtering, explicit intent override, packet summary verification.
- **test_decision_rules.py**: 4 → 24 tests. All new rules tested through `FitnessKnowledgeService.match_decision_rules()`, plus TrainingDecisionRules edge cases (lower body 5kg increment, empty history, `is_lower_body` classification).
- **test_fitness_knowledge.py**: 3 → 26 tests. Knowledge retrieval for female cycle, sleep, supplements, age, injury, macronutrients, progressive overload. Template selection for all goal/level/equipment combos. Coaching case retrieval. Context completeness verification.
- **test_api_integration.py**: NEW — 21 tests. Full FastAPI TestClient with in-memory SQLite + dependency overrides. Auth (register 201/409/422, login 200/401, me 200/401 expired/invalid/no-token), all 9 coach endpoints reject 401, 4 memory endpoints reject 401, chat session creation with token, dashboard 403 cross-user, full auth cycle.
- **test_eval_cases.py**: 2 → 7 tests. Intent classification of all 31 eval cases, actual harness execution through `FitnessKnowledgeService`, eval case count validation, name uniqueness, hard-pass for 4 critical cases, 60% minimum pass rate.

### Phase 7 — Engineering robustness
- **LLM retry**: `core/retry.py` with exponential backoff + jitter. Applied to `coach_reply()` (decorator) and `stream_coach_reply()` (first-chunk health check then stream). Auto-detects retryable errors from status codes and message text.
- **Rate limiting**: `slowapi` integrated in `main.py` + all API route files. Per-endpoint granular limits.
- **Error handling**: `core/errors.py` with 7 custom exception classes and 4 FastAPI handlers (AppError, HTTPException, ValidationError, unhandled Exception). Consistent JSON error format.
- **CI/CD**: `.github/workflows/ci.yml` (lint/type-check/test-matrix/Docker-build/OpenAPI-validate) + `deploy.yml` (Docker push, staging auto, production manual gate).

## How to run

### Backend
```bash
cd fast_api
pip install -r requirements.txt

# Start PostgreSQL + pgvector
docker-compose up -d

# Run FastAPI (with .env for API keys)
uvicorn fast_api.app.main:app --reload --port 8526
```

### Frontend
```bash
cd web
npm install
npm run dev    # Vite dev server on port 5173
```

### Tests
```bash
cd fast_api
# All tests
python -m pytest ../tests/ -v

# Specific areas
python -m pytest ../tests/test_api_integration.py -v
python -m pytest ../tests/test_context_builder.py -v
python -m pytest ../tests/test_eval_cases.py -v
```

## Environment variables

Key variables in `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | postgresql+psycopg://... | PostgreSQL connection |
| `LLM_PROVIDER` | qwen | qwen / deepseek / openai / offline |
| `DASHSCOPE_API_KEY` | — | Qwen API key |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `EMBEDDING_PROVIDER` | qwen | qwen / openai / offline |
| `USE_PGVECTOR` | true | Enable pgvector (set false for SQLite tests) |
| `JWT_SECRET_KEY` | change-me... | HS256 signing key (64+ chars in production) |
| `JWT_EXPIRE_MINUTES` | 1440 | Token lifetime (24h default) |
| `CORS_ORIGINS` | localhost:5173,localhost:8526 | Allowed origins |

## API endpoints

| Method | Path | Auth | Rate limit | Purpose |
|--------|------|------|-----------|---------|
| POST | `/v1/auth/register` | No | 5/min | Create account + get JWT |
| POST | `/v1/auth/login` | No | 5/min | Login + get JWT |
| GET | `/v1/auth/me` | Yes | 30/min | Current user info |
| POST | `/v1/chat/sessions` | Yes | 30/min | Create chat session |
| POST | `/v1/chat/messages` | Yes | 30/min | Send chat message |
| POST | `/v1/chat/messages/stream` | Yes | 15/min | Stream chat response (NDJSON) |
| POST | `/v1/profiles` | Yes | — | Upsert user profile |
| POST | `/v1/checkins/daily` | Yes | — | Record daily check-in |
| POST | `/v1/workouts/logs` | Yes | — | Log workout |
| POST | `/v1/plans/generate` | Yes | 10/min | Generate training plan |
| POST | `/v1/plans/adjust` | Yes | — | Adjust plan from signals |
| GET | `/v1/users/{id}/dashboard` | Yes | — | User dashboard |
| GET | `/v1/agent-runs/{id}` | Yes | — | Agent run detail |
| POST | `/v1/evals/run` | Yes | — | Run eval suite |
| POST | `/v1/memory/items` | Yes | 30/min | Create memory |
| GET | `/v1/memory/items` | Yes | — | List memories |
| GET | `/v1/memory/catalog` | Yes | — | Memory catalog |
| POST | `/v1/memory/search` | Yes | 20/min | Search memories |
| POST | `/v1/agent/context` | Yes | 30/min | Build context packet |
| POST | `/v1/agent/decision` | Yes | — | Log agent decision |
| GET | `/v1/agent/decisions` | Yes | — | List decisions |
| GET | `/health` | No | 30/min | Health check |

## Design decisions

**Rule-first, LLM-second**: Intent classification and profile extraction use keyword/pattern rules first, only falling back to LLM for complex or ambiguous messages. This keeps latency low and behavior deterministic. The LLM is used for final response generation and onboarding conversation.

**Offline fallback everywhere**: When no API key is configured, the system uses hash-based fake embeddings and rule-only responses. Every endpoint works without a live model — responses are just less conversational.

**Single-agent architecture**: Unlike LangGraph-style multi-agent systems, this uses a single `CoachAgentService` orchestrator with sequential nodes. Each node logs structured events to `AgentRunLogger` for observability.

**Safety first**: Messages mentioning medications, cardiac symptoms, or severe pain trigger static safety replies recommending medical consultation. Decision rules carry `safety_level` tags (`caution`, `warning`, `critical`).

**Content-language dual support**: The system handles Chinese and English inputs throughout — keyword matching, exercise name extraction, profile field normalization all work bilingually. Responses default to Chinese since the target audience is Chinese-speaking.

## Future work

- **Streaming retry**: Mid-stream failures are not retried — once the first chunk is received, the stream is trusted. Full stream retry would require buffering all chunks.
- **Rate limit by user ID**: Currently keyed by IP (`get_remote_address`). Authenticated users should use `user_id` for per-user limits.
- **Redis-backed rate limiter**: In-memory limits reset on restart. Production should use Redis.
- **A/B eval framework**: The eval harness exists but isn't wired to compare prompts or model versions side-by-side.
- **Frontend tests**: No frontend test coverage (Jest + React Testing Library).
- **K8s deployment manifest**: The deploy workflow has a placeholder; actual K8s/ECS/Fly.io manifests need to be added.
