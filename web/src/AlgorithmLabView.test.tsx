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
  });
});
