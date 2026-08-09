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
