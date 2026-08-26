import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AlgorithmLabView } from "./AlgorithmLabView";
import { LanguageProvider } from "./LanguageContext";

vi.mock("./api", () => ({
  compareIntent: vi.fn(),
  fetchAgentLabRuns: vi.fn().mockResolvedValue([]),
  fetchAgentLabRun: vi.fn(),
  fetchAgentChallenges: vi.fn().mockResolvedValue({
    cases: 120, passed: 20, component_scores: {}, failure_examples: [], failure_count: 100,
  }),
  fetchAlgorithmSummary: vi.fn().mockResolvedValue({
    intent_inference: { adapter_status: "verified_offline" },
  }),
  fetchIntentEvaluation: vi.fn().mockResolvedValue({
    schema_version: "fitagent-public-intent-evaluation/v1",
    dataset: { name: "agent_challenge_v1", cases: 120, partition: "test", source: "challenge_eval", training_eligible: false, user_messages_exposed: false },
    paths: [
      { id: "rule_only", label: "Rules v2", role: "safety_baseline", exact_pass_rate: 0.1667, risk_score: 0.8333, model_calls: 0, latency_p50_ms: 0, latency_p95_ms: 0 },
      { id: "qwen3_adapter", label: "Qwen3-4B QLoRA", role: "local_adapter_candidate", exact_pass_rate: 0.1333, risk_score: 1, model_calls: 120, latency_p50_ms: 3210.97, latency_p95_ms: 3630.41 },
    ],
    adapter_delta_vs_base: 0.075,
    observations: [],
    limitations: ["Offline fixed-test evidence only."],
    failure_taxonomy: {
      transitions: { deepseek_all: { rescued_from_rule: 22, regressed_from_rule: 3 }, hybrid: { rescued_from_rule: 12, regressed_from_rule: 0 } },
      categories: [{ category: "multi_intent", cases: 20, paths: { rule_only: { exact_pass_rate: 0, check_scores: {} }, deepseek_all: { exact_pass_rate: 0.3, check_scores: {} }, hybrid: { exact_pass_rate: 0.1, check_scores: {} } }, best_observed_paths: ["deepseek_all"], dominant_deepseek_failure: "secondary_intents", actionability: "diagnostic_only_do_not_tune_on_test" }],
      next_data_contract: { required_partition: "development", must_not_copy_fixed_test_prompts: true, minimum_cases_per_priority_category: 30 },
      limitations: [],
    },
    development_protocol: {
      dataset: { cases: 90, partition: "development", training_eligible: false, human_review_status: "not_reviewed" },
      isolation: { passed: true, exact_overlap_count: 0, maximum_character_5gram_jaccard: 0.0612 },
      paths: { rule_only: { cases: 90, exact_pass_rate: 0, check_scores: {} }, rule_with_protocol: { cases: 90, exact_pass_rate: 0.0667, check_scores: {} } },
      categories: {},
      protocol_reason_counts: {},
      field_router: { primary_intent_threshold: 0.8, secondary_intents_threshold: 0.75, risk_authority: "deterministic_rules", low_confidence_action: "request_deepseek_field_review", evaluated_with_live_adapter: false },
      claims: { test_set_used_for_tuning: false, production_uplift: false, human_reviewed: false },
      limitations: [],
    },
  }),
}));

describe("AlgorithmLabView", () => {
  beforeEach(() => localStorage.removeItem("ai_fitness_language"));

  it("shows aggregate intent paths and their data boundary", async () => {
    render(<LanguageProvider><AlgorithmLabView /></LanguageProvider>);

    await waitFor(() => expect(screen.getByRole("heading", { name: "意图算法批量对照" })).toBeInTheDocument());
    expect(screen.getByText("Rules v2")).toBeInTheDocument();
    expect(screen.getAllByText("Qwen3-4B QLoRA")).toHaveLength(2);
    expect(screen.getByText(/training_eligible=false/)).toBeInTheDocument();
    expect(screen.getByText(/7.50 pp/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "错误分桶与路径救回" })).toBeInTheDocument();
    expect(screen.getByText("secondary_intents")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "独立开发集与字段路由" })).toBeInTheDocument();
    expect(screen.getByText(/live_adapter_evaluated=false/)).toBeInTheDocument();
  });
});
