import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AlgorithmLabView } from "./AlgorithmLabView";
import { compareIntent } from "./api";

vi.mock("./api", () => ({
  fetchAgentLabRuns: vi.fn().mockResolvedValue([]),
  fetchAgentChallenges: vi.fn().mockResolvedValue(null),
  fetchIntentEvaluation: vi.fn().mockResolvedValue(null),
  fetchAlgorithmSummary: vi.fn().mockResolvedValue({ intent_inference: { adapter_status: "verified_offline" } }),
  fetchAgentLabRun: vi.fn(), compareIntent: vi.fn(),
}));

it("explains developer-only scope and keeps offline status distinct from live availability", async () => {
  render(<AlgorithmLabView />);
  expect(await screen.findByText("已离线验证 · 非在线状态")).toBeInTheDocument();
  expect(screen.getByText(/不是训练安排页面/)).toBeInTheDocument();
  const input = screen.getByRole("textbox", { name: "单例意图对比（不计入离线指标）" });
  expect(input).toHaveValue("我膝盖疼，但明天还能继续深蹲吗？");
  expect(screen.getByText(/可能消耗你配置的模型额度/)).toBeInTheDocument();
  expect(compareIntent).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "   " } });
  expect(screen.getByRole("button", { name: "运行意图对比" })).toBeDisabled();
});
