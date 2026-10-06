import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ResponsibilityView } from "./ResponsibilityView";
import { createResponsibilityDemo } from "./responsibilityDemo";
import { api } from "./api";

vi.mock("./api", async importOriginal => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, api: vi.fn() };
});

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); vi.mocked(api).mockReset(); });

it("opens the saved background run read-only from existing history", async () => {
  const demo = createResponsibilityDemo();
  const history = await demo.gateway.history();
  const runId = "11111111-1111-4111-8111-111111111111";
  history[0].execution_trace_run_id = runId;
  vi.mocked(api).mockResolvedValue({ run_id: runId, status: "interrupted", events: [], coverage: {} });
  const decide = vi.spyOn(demo.gateway, "decide");
  render(<ResponsibilityView userId="owner" gateway={{ ...demo.gateway, history: async () => history }} />);
  const entry = await screen.findByText("完整执行追踪");
  expect(api).not.toHaveBeenCalled();
  fireEvent.click(entry);
  await waitFor(() => expect(api).toHaveBeenCalledWith(`/v1/agent-runs/${runId}/trace`));
  expect(decide).not.toHaveBeenCalled();
});

it("does not invent trace links for history without a saved run", async () => {
  const demo = createResponsibilityDemo();
  render(<ResponsibilityView userId="owner" gateway={demo.gateway} />);
  await screen.findByRole("button", { name: "审阅并批准" });
  expect(screen.queryByText("完整执行追踪")).not.toBeInTheDocument();
  expect(api).not.toHaveBeenCalled();
});
