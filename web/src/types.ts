// ---- API response types (mirrors backend schemas) ----
export type TrainingExercise = { name?: string; sets?: number; reps?: number | string; duration_minutes?: number };
export type TrainingPlanData = { training_days?: Array<{ date?: string; day?: number; name?: string; exercises?: TrainingExercise[] }> };

export type SessionState = {
  session_id: string;
  user_id: string;
  title?: string;
  created_at?: string;
};

export type Dashboard = {
  profile_complete: boolean;
  profile: Record<string, any>;
  missing_slots: string[];
  today_plan: Record<string, any>;
  active_plan?: { plan_id: string; status: string; plan: TrainingPlanData } | null;
  timezone?: string;
  latest_checkin: Record<string, any> | null;
  recent_memories: Array<Record<string, any>>;
  progress: Record<string, any>;
  coach_suggestions: string[];
};

export type AgentTraceItem = {
  id: string;
  type: "status" | "step" | "tool_call" | "error" | "done" | "execution_event";
  title: string;
  summary: string;
  latency_ms?: number;
  metadata?: Record<string, any>;
};

export type AgentRunDetail = {
  id: string;
  user_id: string;
  session_id?: string | null;
  run_type: string;
  status: string;
  nodes: Array<Record<string, any>>;
  summary?: string | null;
  error?: string | null;
  log_path?: string | null;
  tool_calls: Array<Record<string, any>>;
  started_at?: string;
  completed_at?: string | null;
};

export type ChatMessage = {
  id?: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
  execution_events?: ExecutionEvent[];
  agent_run_id?: string | null;
};

export type RecordedTrace = {
  schema_version: string; run_id: string; status: string;
  write_receipts?: Array<{ kind: string; state: string; verification?: string; record_id?: string; reason?: string }>;
  events: Array<{
    event_id: string; order: number; parent_id: string; child_id?: string | null;
    step_id?: string | null; name: string; phase: string; status: string; source: string;
    recorded_at: string; latency_ms: number; recorded_sequence?: number | null;
    summary: string; input: unknown; output: unknown; error: unknown;
  }>;
  snapshot: unknown;
  coverage: { saved_nodes: number; saved_tool_calls: number; snapshot_available: boolean;
    exact_model_requests: boolean; ordering: string; limitations: string[];
    damaged_tail?: boolean; liveness?: string };
};

export type ExecutionEvent = {
  type: "execution_event"; name: string; status: "pending" | "running" | "completed" | "failed" | "blocked" | "skipped" | "outcome_unknown";
  source: "runtime" | "rule" | "tool" | "user" | "model_summary";
  recorded_at: string; summary: string; details: Record<string, unknown>;
};

export type AuthUser = {
  user_id: string;
  email: string;
  username?: string | null;
  display_name: string;
  avatar_url?: string | null;
  created_at?: string;
};

export type PlanResponse = {
  plan_id: string;
  status: string;
  plan: Record<string, any>;
  rationale: string;
};

export type CheckinResult = {
  checkin_id: string;
  auto_adjusted: boolean;
  idempotent_replay: boolean;
  adjustment_proposal?: { status: string; reason?: string; approval_id?: string };
  invalidated_approvals?: string[];
};

// ---- UI view state ----

export type ViewName = "chat" | "dashboard" | "checkin" | "workout" | "plan" | "settings" | "account" | "algorithm" | "responsibilities";

export type Responsibility = {
  id: string; status: string; objective: string;
  configuration: {
    timezone: string; starts_at: string; ends_at: string; next_wake_at: string | null;
    weekday: number; hour: number; minute: number; source_instruction: string;
  };
  progress: { reviews_completed?: number; last_review?: {
    execution_events?: ExecutionEvent[];
    scheduled_at: string; week_start: string; week_end: string;
    memories?: Array<{ id: string; summary: string; evidence?: Array<{ table: string; id: string }> }>;
    adjustment_proposal?: { status: string; reason?: string; approval_id?: string };
  } };
};
export type ApprovalActivity = {
  approval_id: string; tool_name: string; tool_description: string;
  status: string; created_at: string; expires_at: string;
  input_preview: { plan_id?: string; day_date?: string; reduce_by?: number; reason?: string };
  context: { baseline_plan?: TrainingPlanData; candidate_plan?: TrainingPlanData; execution_events?: ExecutionEvent[]; responsibility_id?: string; review_signal?: { average_fatigue?: number; evidence?: Array<{ id: string; table: string }> } };
  job_id: string | null; job_status: string | null;
  execution_trace_run_id?: string | null;
  job_attempts?: number | null;
  result: { status?: string; verified?: boolean; reason?: string; day_date?: string };
  error: string | null;
};
export type WeeklyResponsibilityInput = {
  source_instruction: string; weeks: number; weekday: number; hour: number; minute: number;
};

export type UsageSummary = {
  event_date: string;
  user_used: number;
  user_limit: number;
  global_used: number;
  global_limit: number;
  live_calls_available: boolean;
  fallback_mode: string;
};

export type AlgorithmSummary = {
  release_stage: string;
  disclaimer: string;
  datasets: Array<{ name: string; size: number; source: string; status: string }>;
  metrics: Array<{ name: string; value: number; total?: number; unit?: string; source: string }>;
  business_outcomes: { label: string; online_claim: boolean };
  dpo: { enabled: boolean; minimum_reviewed_pairs: number; current_reviewed_pairs: number };
  intent_inference: {
    architecture: string;
    adapter_status: string;
    adapter_model: string;
    safety_authority: string;
    online_result_claimed: boolean;
  };
};

export type AlgorithmCompare = {
  rule_baseline: { primary_intent: string; risk_level: string; confidence: number };
  runtime_decision: { primary_intent: string; secondary_intents: string[]; risk_level: string; confidence: number };
  routing: {
    final_source: string;
    local_model_status: string;
    local_model_used: boolean;
    local_model_version?: string | null;
    local_model_usage: Record<string, number>;
    adapter_fallback_reason?: string | null;
    adapter_http_status?: number | null;
    deepseek_used: boolean;
    rules_evaluated: boolean;
    safety_override_applied: boolean;
    safety_override_reasons: string[];
  };
  latency_ms: Record<string, number>;
  disclaimer: string;
};

export type IntentEvaluationSummary = {
  schema_version: string;
  dataset: {
    name: string;
    cases: number;
    partition: string;
    source: string;
    training_eligible: false;
    user_messages_exposed: false;
  };
  paths: Array<{
    id: string;
    label: string;
    role: string;
    exact_pass_rate: number;
    risk_score: number;
    model_calls: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
  }>;
  adapter_delta_vs_base: number;
  observations: string[];
  limitations: string[];
  failure_taxonomy?: {
    transitions: Record<string, { rescued_from_rule: number; regressed_from_rule: number }>;
    categories: Array<{
      category: string;
      cases: number;
      paths: Record<string, { exact_pass_rate: number; check_scores: Record<string, number> }>;
      best_observed_paths: string[];
      dominant_deepseek_failure?: string | null;
      actionability: string;
    }>;
    next_data_contract: {
      required_partition: string;
      must_not_copy_fixed_test_prompts: boolean;
      minimum_cases_per_priority_category: number;
    };
    limitations: string[];
  } | null;
  development_protocol?: {
    dataset: { cases: number; partition: string; training_eligible: false; human_review_status: string };
    isolation: { passed: boolean; exact_overlap_count: number; maximum_character_5gram_jaccard: number };
    paths: Record<string, { cases: number; exact_pass_rate: number; check_scores: Record<string, number> }>;
    categories: Record<string, Record<string, { cases: number; exact_pass_rate: number; check_scores: Record<string, number> }>>;
    protocol_reason_counts: Record<string, number>;
    field_router: { primary_intent_threshold: number; secondary_intents_threshold: number; risk_authority: string; low_confidence_action: string; evaluated_with_live_adapter: boolean };
    claims: { test_set_used_for_tuning: false; production_uplift: false; human_reviewed: false };
    limitations: string[];
  } | null;
};

export type AgentFinding = {
  code: string;
  severity: "info" | "low" | "medium" | "high";
  title: string;
  detail: string;
  node: string;
};

export type AgentRunAnalysis = {
  run_id: string;
  run_type: string;
  status: string;
  started_at: string;
  completed_at?: string | null;
  summary: string;
  node_count: number;
  tool_count: number;
  total_latency_ms: number;
  decision: {
    intent: string;
    planner_mode: string;
    memory_count: number;
    knowledge_count: number;
    tool_names: string[];
    verifier_issue_count: number;
    guardrail_action: string;
  };
  findings: AgentFinding[];
  timeline: Array<{
    order: number;
    node: string;
    phase: string;
    phase_label: string;
    status: string;
    latency_ms: number;
    summary: string;
  }>;
  privacy: string;
};

export type AgentChallengeSummary = {
  experiment_id: string;
  source: string;
  partition: string;
  training_eligible: false;
  cases: number;
  passed: number;
  pass_rate: number;
  component_scores: Record<string, number>;
  categories: Array<{ name: string; cases: number; passed: number; pass_rate: number }>;
  failure_count: number;
  failure_examples: Array<{
    case_id: string;
    category: string;
    checks: Record<string, boolean>;
    expected: Record<string, unknown>;
    actual: Record<string, unknown>;
    user_message: string;
  }>;
};
