import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { RecordedTracePanel } from "./RecordedTracePanel";

vi.mock("./api", () => ({ api: vi.fn() }));
beforeEach(() => vi.mocked(api).mockReset());
const trace = { run_id: "run-a", status: "completed", events: [{ event_id: "e1", order: 1,
  name: "records.read", status: "failed", latency_ms: 4, parent_id: "run-a", child_id: "training",
  summary: "读取失败", source: "tool", input: { date: "2026-10-04" }, output: { reason: "timeout" },
}], snapshot: {}, coverage: {} };

it("loads only when expanded and renders inspectable recorded data", async () => {
  vi.mocked(api).mockResolvedValue(trace);
  render(<RecordedTracePanel runId="run-a" />);
  expect(api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("完整执行追踪"));
  await screen.findByText("1. records.read · failed · 4 ms");
  expect(api).toHaveBeenCalledWith("/v1/agent-runs/run-a/trace");
  expect(screen.getByText(/不能精确复现/)).toBeInTheDocument();
  expect(screen.getByText("training")).toBeInTheDocument();
});

it("shows errors and allows an explicit read-only refresh", async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error("denied")).mockResolvedValue(trace);
  render(<RecordedTracePanel runId="run-a" />);
  fireEvent.click(screen.getByText("完整执行追踪"));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByText("刷新记录"));
  await screen.findByText("1. records.read · failed · 4 ms");
});

it("does not display a late response from a previous run", async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockReturnValue(new Promise(r => { resolve = r; }));
  const view = render(<RecordedTracePanel runId="run-a" />);
  fireEvent.click(screen.getByText("完整执行追踪"));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  view.rerender(<RecordedTracePanel runId="run-b" />);
  await act(async () => resolve(trace));
  expect(screen.queryByText("1. records.read · failed · 4 ms")).not.toBeInTheDocument();
});

it("shows incomplete journal evidence without suggesting a retry", async () => {
  vi.mocked(api).mockResolvedValue({ ...trace, status: "unconfirmed",
    coverage: { damaged_tail: true, liveness: "not_checked" } });
  render(<RecordedTracePanel runId="request-a" />);
  fireEvent.click(screen.getByText("完整执行追踪"));
  await screen.findByText(/记录尾部缺损/);
  expect(screen.getByText(/不会自动重跑/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重跑" })).not.toBeInTheDocument();
});

it("explains commit evidence separately from successful tool execution", async () => {
  vi.mocked(api).mockResolvedValue({ ...trace, events: [{ ...trace.events[0],
    name: "training.log.write", status: "success",
    output: { write_receipt: { state: "unconfirmed", may_repeat_writes: false } },
  }] });
  render(<RecordedTracePanel runId="run-a" />);
  fireEvent.click(screen.getByText("完整执行追踪"));
  await screen.findByText(/工具成功不等于提交成功/);
  expect(screen.queryByRole("button", { name: "重跑" })).not.toBeInTheDocument();
});

it("shows a committed workout without claiming the interrupted request completed", async () => {
  vi.mocked(api).mockResolvedValue({ ...trace, status: "unconfirmed",
    write_receipts: [{ kind: "workout_log", state: "committed", verification: "independent_committed_read", record_id: "workout-1" }] });
  render(<RecordedTracePanel runId="request-a" />);
  fireEvent.click(screen.getByText("完整执行追踪"));
  await screen.findByText(/训练记录已提交；这不代表整个请求已完成/);
  expect(screen.getByText(/workout-1/)).toBeInTheDocument();
});

it("does not present approval as a saved plan", async () => {
  vi.mocked(api).mockResolvedValue({ ...trace, write_receipts: [{ kind: "plan_adjustment",
    state: "unconfirmed", reason: "approval_not_executed" }] });
  render(<RecordedTracePanel runId="run-a" />);
  fireEvent.click(screen.getByText("完整执行追踪"));
  await screen.findByText(/用户批准不等于计划已更新/);
  expect(screen.queryByText(/计划调整已提交/)).not.toBeInTheDocument();
});

it("renders model ownership and its bounded input within the child trace", async () => {
  vi.mocked(api).mockResolvedValue({ ...trace, events: [{ ...trace.events[0],
    name: "model.start", child_id: "child-training", parent_id: "parent-review", step_id: "training-model-1",
    input: { message_batches: [[{ role: "human", content: "合成复盘输入" }]], exact_request: false },
  }] });
  render(<RecordedTracePanel runId="run-a" />);
  fireEvent.click(screen.getByText("完整执行追踪"));
  await screen.findByText("child-training");
  expect(screen.getByText("parent-review")).toBeInTheDocument();
  expect(screen.getByText("training-model-1")).toBeInTheDocument();
  expect(screen.getByText(/合成复盘输入/)).toBeInTheDocument();
});

it("shows local model interruption without claiming remote failure or success", async () => {
  vi.mocked(api).mockResolvedValue({ ...trace, status: "unconfirmed", events: [{ ...trace.events[0],
    name: "model.interrupted", status: "outcome_unknown",
    summary: "本地模型等待已中断；远端执行结果未确认。",
    output: { model_call_id: "cancelled-call", remote_result_confirmed: false, local_stop_reason: "CancelledError" },
  }] });
  render(<RecordedTracePanel runId="run-a" />);
  fireEvent.click(screen.getByText("完整执行追踪"));
  await screen.findByText("本地模型等待已中断；远端执行结果未确认。");
  expect(screen.getByText(/remote_result_confirmed/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重跑" })).not.toBeInTheDocument();
});
