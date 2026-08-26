// ---- API response types (mirrors backend schemas) ----

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
  latest_checkin: Record<string, any> | null;
  recent_memories: Array<Record<string, any>>;
  progress: Record<string, any>;
  coach_suggestions: string[];
};

export type AgentTraceItem = {
  id: string;
  type: "status" | "step" | "tool_call" | "error" | "done";
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
  status: string;
  checkin_id: string;
  auto_adjusted: boolean;
};

// ---- UI view state ----

export type ViewName = "chat" | "dashboard" | "checkin" | "workout" | "account" | "algorithm";

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
