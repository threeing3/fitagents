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
