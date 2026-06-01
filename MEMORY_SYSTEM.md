# AI Fitness Agent Memory System

This project uses PostgreSQL + pgvector as the single data store for business data, long-term memory, retrieval context, and agent decision traces.

## Layers

- L0 current conversation: `chat_messages`
- L1 core profile: `user_profiles`, `memory_blocks`
- L2 current cycle: `training_plans`, `workout_sessions`, `exercise_logs`, `recovery_logs`
- L3 memory catalog: `memory_catalog`
- L4 structured history: workout, nutrition, recovery, symptom, and body metric tables
- L5 semantic memory: `long_term_memories` with pgvector embeddings
- L6 archive/export: `memory_exports`

## Existing Tables Reused

The system intentionally reuses existing tables instead of duplicating responsibilities:

- `long_term_memories` acts as the memory item table. It now includes `category`, `summary`, `recency_score`, and `parent_memory_id`.
- `training_plans`, `workout_logs`, `meal_logs`, `daily_checkins`, `agent_runs`, and `tool_calls` remain compatible with the current MVP.
- New normalized tables such as `workout_sessions`, `exercise_logs`, and `nutrition_daily_summaries` support future analytics without breaking old JSONB logs.

## New Core Tables

- `memory_blocks`: short high-value profile, goal, risk, preference, and current-plan blocks loaded by default.
- `memory_catalog`: a directory of available memories. The agent reads this before deeper retrieval.
- `risk_notes`: high-priority safety memory for pain, symptoms, disease, medication, and training constraints.
- `symptom_logs`: event-level symptom records.
- `workout_sessions` and `exercise_logs`: normalized training history for progression decisions.
- `nutrition_logs` and `nutrition_daily_summaries`: food records and daily calorie/macro summaries.
- `recovery_logs`: sleep, fatigue, soreness, stress, and resting heart rate.
- `agent_decisions`: explainable decisions for plan generation, adjustment, deload, progression, and reviews.
- `memory_exports`: placeholder for a future Fitness Memory Passport export.

## Runtime Flow

Before a full coach reply, the backend builds a context packet:

```text
IntentRouter
-> MemoryCatalog
-> FitnessRetrievalService
-> ContextBuilder
-> LLM
```

The packet is also written into `agent_runs.nodes` as the `ContextBuilder` node so the debug panel can show what context was used.

## Intent Strategy

`IntentRouter` is rule-first for the MVP. It supports:

- `training_log`
- `training_plan`
- `progression_decision`
- `nutrition_advice`
- `nutrition_log`
- `recovery_check`
- `injury_or_risk`
- `weekly_review`
- `monthly_review`
- `memory_query`
- `general_chat`

## Context Strategy

Default context:

- core profile
- active plan
- memory catalog
- active risk notes
- relevant semantic memories

Intent-specific context:

- Training/progression: recent workouts, exercise history, recovery, symptoms.
- Nutrition: recent nutrition summaries, nutrition memories, target macros.
- Risk/pain: active risk notes, symptom logs, recovery, relevant risk memory.
- Weekly/monthly review: training, nutrition, recovery, symptoms, active plan.

The agent should not load full chat history or full database history by default.

## Decision Logging

Important agent actions should be written to `agent_decisions` with:

- `decision_type`
- `input_summary`
- `context_used`
- `decision_result`
- `reason`
- `confidence_score`

Plan generation and plan adjustment already write decision records.

## Progression Rules

The MVP rules are implemented in `TrainingDecisionRules`:

- Increase load when recent work is completed cleanly with average RPE <= 8.5 and no pain.
- Hold load when RPE is high or recovery is poor.
- Deload when pain appears, failures repeat, or RPE is very high.
- Prioritize risk warning for chest tightness, dizziness, sharp pain, numbness, breathing difficulty, or severe symptom logs.

## API

Memory/debug endpoints:

- `POST /v1/memory/items`
- `GET /v1/memory/items`
- `GET /v1/memory/catalog`
- `POST /v1/memory/search`
- `POST /v1/agent/context`
- `POST /v1/agent/decision`
- `GET /v1/agent/decisions`

Existing coach endpoints remain unchanged.

## Fitness Memory Passport

`memory_exports` reserves the future export path. The intended package shape is:

```text
fitness_memory_passport.zip
|-- manifest.json
|-- profile.json
|-- training_history.jsonl
|-- nutrition_history.jsonl
|-- body_metrics.jsonl
|-- recovery_logs.jsonl
|-- memory_blocks.json
|-- long_term_memories.jsonl
|-- memory_catalog.json
|-- agent_decisions.jsonl
`-- README_schema.md
```

Health and risk data should be clearly marked as sensitive when export is implemented.
