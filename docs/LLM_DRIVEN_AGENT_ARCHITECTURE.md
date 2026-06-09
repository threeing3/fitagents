# Architecture: LLM-Driven Tool-Use Loop (Claude Code Pattern)

## What changes

The current architecture is **code-driven**: AgentPlanner uses keyword matching to decide which tools to
run, in what order. The LLM only generates the final coach reply.

The new architecture is **LLM-driven**: the system prompt lists available tools with descriptions and
schemas, the LLM iteratively picks tools and observes results, until it decides to produce a final
response. The host (code) executes the tools and injects results back into the conversation.

## RuntimeRouter: Dynamic Runtime Selection

The current product uses a lightweight `RuntimeRouter` before entering either
runtime. This avoids using one global boolean for all requests.

- `/v1/chat/messages` returns a complete non-streaming response. The backend
  routes the current message first, calls either `_handle_chat_llm_agent(...)`
  or `_handle_chat_code_driven(...)`, and appends `runtime_route` to the
  response payload.
- `/v1/chat/messages/stream` returns NDJSON streaming events. The backend emits
  one `runtime_route` event before the existing `status`, `step`,
  `answer_delta`, and `done` events. Existing frontend code can ignore this new
  event without breaking the display.
- Simple concept explanation, small talk, and motivation can use
  `llm_driven`.
- Training plans, nutrition logging, profile updates, long-term memory,
  plan edits, recovery status, and safety or medical-risk messages use
  `code_driven`.
- Unknown cases default to `code_driven`, because this fitness agent should not
  miss user state, plan changes, memory writes, or safety signals.
- The first version does not call an LLM for routing. This avoids extra cost,
  latency, and classifier instability. The route logic lives in
  `fast_api/app/services/runtime_router.py` and is covered by unit tests.

Runtime config:

- `AGENT_RUNTIME_MODE=auto`: default, use RuntimeRouter.
- `AGENT_RUNTIME_MODE=llm_driven`: force LLM-driven for debugging.
- `AGENT_RUNTIME_MODE=code_driven`: force Code-driven for debugging.

`USE_LLM_DRIVEN_AGENT` remains as a legacy compatibility flag. New development
and debugging should prefer `AGENT_RUNTIME_MODE`.

## LLM Planner for Code-Driven Runtime

The code-driven runtime now has an agentic planner layer. It is no longer only
a fixed keyword workflow.

Flow:

1. `RuntimeRouter` decides whether the turn enters `llm_driven` or
   `code_driven`.
2. For `code_driven`, `LLMPlanner` receives the current message, runtime route,
   profile summary, active plan summary, available `ToolSpec` contracts, and
   safety/write constraints.
3. `LLMPlanner` returns structured JSON with `intent`, `selected_tools`,
   `tool_order`, `write_intent`, `safety_level`, `plan_generation_allowed`, and
   a visible `reasoning_summary`.
4. `PlannerVerifier` validates and repairs the plan before any tool executes:
   unknown tools are rejected, `memory.write` must follow `memory.verify`,
   `plan.verify` must follow `plan.generate`, `guardrail.check` is enforced,
   and `response.persist` is forced to the final step.
5. `AgentExecutor` executes only registered host tools. The LLM planner never
   touches the database directly and cannot bypass schema validation, retry,
   repair, guardrails, replay snapshots, or logs.
6. If the planner model is unavailable, returns invalid JSON, or chooses an
   illegal tool, the runtime falls back to the existing rule planner and records
   `planner_fallback=true`.

Config:

- `CODE_DRIVEN_PLANNER=llm`: default, use LLM Planner first.
- `CODE_DRIVEN_PLANNER=rule`: force deterministic rule planner.
- `CODE_DRIVEN_PLANNER_FALLBACK=rule`: default, fall back when LLM planning
  fails.
- `CODE_DRIVEN_PLANNER_FALLBACK=error`: useful for debugging planner failures.

Observability:

- Agent run nodes include `LLMPlanner`, `PlannerVerifier`, and
  `PlannerFallback` when fallback occurs.
- Replay snapshots include `llm_planner_raw`, `planner_verified_plan`,
  `planner_repair_actions`, and `planner_fallback_reason`.
- Logs show the visible tool decision summary. Hidden model chain-of-thought is
  not exposed; only `reasoning_summary` is stored.

## Why this is smarter

The old code-driven pipeline always runs the same 13 tools in the same order. If a user says
"hello" the agent still runs profile.extract, memory.verify, memory.write, context.build, plan.decide...
That's wasteful and slow.

The new LLM-driven loop adapts to the situation:
- User says "hello" → LLM sees no need for tools, responds directly (1 LLM call, ~500ms)
- User says "generate a plan" → LLM calls context.build → plan.decide → plan.generate → plan.verify → coach.reply (6 LLM calls, ~3s)
- User says "what's my weight?" → LLM calls context.build → coach.reply (2 LLM calls, ~1s)

The LLM decides what's needed based on the actual request, not a fixed plan.

## How it works (Claude Code pattern)

```
SYSTEM PROMPT (injected at start):
  "You are a fitness coach. You have access to these tools:
   - profile.extract: Extract profile fields from user messages
   - memory.verify: Verify memory candidates before writing
   - memory.write: Write verified memories
   - context.build: Build intent-specific context packet
   - plan.decide: Decide if plan generation is allowed
   - plan.generate: Generate a training plan
   - plan.verify: Verify plan structure and safety
   - plan.repair: Repair fixable plan issues
   - guardrail.check: Check response safety
   - response.persist: Persist the final response
   
   To use a tool, respond with:
   <tool_call>{"name": "context.build", "input": {"message_chars": 50}}</tool_call>
   
   After all necessary tool calls, produce the final coaching response.
   Think step by step: do you need to look up the user's profile? Their plan?
   Their recent workouts? Only call tools that are necessary."

EXECUTION LOOP:
  1. Send system prompt + user message to LLM
  2. LLM responds with either:
     a. <tool_call>...</tool_call> → Parse, execute tool, inject <tool_result>...</tool_result>, go to 2
     b. Plain text → This is the final reply, exit loop
  3. Max iterations: 10 (safety limit)
  4. Each tool result is injected as a user-role message with the result JSON
```

## Implementation strategy

### Phase 1: Build the LLM-driven agent service (NEW file)

`fast_api/app/services/llm_agent.py`

- Contains `LLMAgentService` class
- Takes ModelProvider + ToolRegistry + DB session
- `async def run(message: str, user_id: UUID, session_id: UUID) -> AgentResult`
- The run() loop:
  1. Build system prompt with tool definitions
  2. Call LLM with messages=[system, user]
  3. Parse response: look for <tool_call> tags
  4. If tool call found: execute tool, append result as user message, loop
  5. If no tool call: return as final response
  6. Track timeline, nodes, tool_calls just like current runtime

### Phase 2: Wire into coach_agent.py (MODIFY existing)

Replace the existing `handle_chat_message` and `stream_chat_events` with calls to the new
LLM-driven agent. Keep the old code-driven implementations as `_legacy_handle_chat_message`
and `_legacy_stream_chat_events` for fallback purposes.

Add a config flag: `LLM_DRIVEN_AGENT=true` in settings. If false, use legacy code-driven pipeline.
This allows A/B comparison.

### Phase 3: Tool definitions for the LLM prompt

Build a function that takes the ToolRegistry and produces a system prompt with tool descriptions.
Each tool gets:
- Name
- Description
- Input schema (simplified)
- When to use (contextual guidance)

Example:
```
## Available Tools

### context.build
Build an intent-specific context packet containing user profile, memories,
active plan, risk notes, and relevant knowledge.

Input: {"message_chars": <int>}
Output: {"intent": "...", "core_profile": {...}, "active_plan": {...}, ...}

Use when: you need to know the user's profile, training plan, or recent state
before giving advice. Almost always needed for training/nutrition/recovery questions.

### plan.generate
Generate and persist a new training plan.

Input: {"reason": "<string>"}
Output: {"plan_id": "<uuid>", "active_plan": {...}}

Use when: the user explicitly asks for a new training plan AND no active plan
exists. Do NOT use for plan queries or adjustments to existing plans.
```

### Phase 4: Safety and guardrails

The guardrail.check tool is included in the tool registry. The LLM is instructed to call it
BEFORE producing the final response. If guardrail triggers BLOCK, the system injects the
replacement text and tells the LLM to use it instead.

### Phase 5: Tool result format

When the LLM calls a tool, the result is injected as:
```
<tool_result tool="context.build">
{
  "intent": "training_plan",
  "core_profile": {...},
  "active_plan": null,
  "knowledge_context": {...}
}
</tool_result>

Continue your response. Call more tools if needed, or produce the final coaching reply.
```

## Comparison: Old vs New

|                   | Old (Code-Driven) | New (LLM-Driven) |
|-------------------|-------------------|------------------|
| Tool decisions    | Keyword matching  | LLM reasoning    |
| Execution order   | Fixed for all inputs | Dynamic per input |
| Simple "hello"    | 13 tools, ~2s    | 0 tools, ~500ms  |
| Plan request      | 13 tools, ~2s    | 5-6 tools, ~3s   |
| Error recovery    | Fixed repair handlers | LLM can retry differently |
| Cost (tokens)     | 1 LLM call       | 2-8 LLM calls    |
| Latency           | Predictable      | Variable         |
| Adaptability      | Low (fixed plan) | High (LLM decides) |

## Trade-offs

**Advantages of LLM-driven:**
- More adaptive: the agent uses only the tools it needs
- Smarter recovery: LLM can try different approaches on failure
- Better for complex/novel requests that don't fit keyword patterns
- Closer to state-of-the-art agent architectures

**Disadvantages of LLM-driven:**
- Higher token cost (multiple LLM calls per user message)
- Higher latency for complex requests
- Less predictable (same input may trigger different tool sequences)
- LLM might skip safety-critical tools (partially mitigated by guardrail.check tool definition)

**Why this is worth it:**
The code-driven approach works for structured domains, but it can't handle edge cases.
A user saying "I tweaked my knee yesterday during squats, and today it still hurts when I
walk downstairs, what should I do?" — the current keyword matcher sees "squats" and classifies
it as training_log, missing the injury context. An LLM-driven agent would recognize this as
injury_or_risk, call context.build to get the injury history, and respond appropriately.

## Migration plan

1. Build LLMAgentService as a standalone module → test independently
2. Add `LLM_DRIVEN_AGENT` config flag → default to False (existing behavior)
3. Wire into coach_agent.py → both paths available
4. Test with real LLM → compare quality and latency
5. Once validated, set default to True

## Implementation

See the following files that implement this architecture:
- `fast_api/app/services/llm_agent.py` — The LLM-driven agent service
- `fast_api/app/core/config.py` — Added `LLM_DRIVEN_AGENT` setting
- `fast_api/app/services/coach_agent.py` — Modified to dispatch between old and new
