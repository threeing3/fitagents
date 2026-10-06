import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSubagentCatalogs, fetchSubagentRequestStatus, reconcileSubagentCatalog, reconcileSubagentRequest, requestSubagentTurn, stopSubagentTree } from "./api";
import { SubagentCatalogPanel } from "./SubagentCatalogPanel";
import { saveChildReceipt } from "./subagentRequestReceipt";

vi.mock("./api", () => ({ fetchSubagentCatalogs: vi.fn(), reconcileSubagentCatalog: vi.fn(), requestSubagentTurn: vi.fn(), fetchSubagentRequestStatus: vi.fn(), cancelSubagentRequest: vi.fn(), reconcileSubagentRequest: vi.fn(), stopSubagentTree: vi.fn() }));
vi.mock("./AuthContext", () => ({ useAuth: () => ({ user: { user_id: "owner" } }) }));
beforeEach(() => { sessionStorage.clear(); vi.resetAllMocks(); });
const fetchRows = vi.mocked(fetchSubagentCatalogs);
describe("persisted child catalog", () => {
  it("allows explicit tree stop while chat is busy without asserting it already stopped", async () => {
    const row = { parent_id: "p", revision: 5, recorded_at: "now", stop_control: { protocol: 1 }, children: [{ child_id: "one", domain: "evidence_analysis", status: "running" }] };
    fetchRows.mockResolvedValueOnce([row]).mockResolvedValueOnce([row]);
    vi.mocked(stopSubagentTree).mockResolvedValueOnce({ status: "cancel_requested" });
    render(<SubagentCatalogPanel sessionId="s" busy={true} />);
    fireEvent.click(screen.getByText("子任务记录"));
    fireEvent.click(await screen.findByText("停止协作"));
    expect(stopSubagentTree).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("确认停止协作，不重跑"));
    await screen.findByText("协作停止请求已接收；请刷新目录确认最终状态。");
    expect(stopSubagentTree).toHaveBeenCalledWith("s", row);
  });
  it("retains reconciled advice status while refreshing a failed catalog", async () => {
    saveChildReceipt("owner", "s", "12345678-1234-4123-8123-123456789abc", "p");
    const row = { parent_id: "p", revision: 4, recorded_at: "now", execution_lease: { protocol: 1 }, children: [{ child_id: "one", domain: "training", status: "failed" }] };
    fetchRows.mockResolvedValueOnce([row]).mockResolvedValueOnce([row]);
    vi.mocked(reconcileSubagentRequest).mockResolvedValueOnce({ status: "recorded", result: { status: "failed", no_business_writes: true } });
    render(<SubagentCatalogPanel sessionId="s" busy={false} />);
    fireEvent.click(screen.getByText("子任务记录"));
    fireEvent.click(await screen.findByText("补充咨询"));
    fireEvent.click(screen.getByText("核对请求回执"));
    fireEvent.click(screen.getByText("确认关闭孤儿回执"));
    expect(await screen.findByText("未完成咨询或已被安全规则拦截")).toBeVisible();
    await waitFor(() => expect(fetchRows).toHaveBeenCalledTimes(2));
    expect(screen.getByText("未完成咨询或已被安全规则拦截")).toBeVisible();
  });
  it("discovers older receipts without manufacturing a runnable catalog", async () => {
    saveChildReceipt("owner", "s", "12345678-1234-4123-8123-123456789abc", "old-parent");
    fetchRows.mockResolvedValueOnce([]);
    vi.mocked(fetchSubagentRequestStatus).mockResolvedValueOnce({ status: "unconfirmed" });
    render(<SubagentCatalogPanel sessionId="s" busy={false} />);
    fireEvent.click(screen.getByText("子任务记录"));
    fireEvent.click(await screen.findByText("目录外待确认请求 · 1"));
    fireEvent.click(screen.getByText("查询原请求"));
    await waitFor(() => expect(fetchSubagentRequestStatus).toHaveBeenCalledWith("s", "12345678-1234-4123-8123-123456789abc"));
    expect(screen.queryByText("继续只读咨询")).not.toBeInTheDocument();
    expect(requestSubagentTurn).not.toHaveBeenCalled();
  });
  it("keeps a recovered old receipt visible after clearing its pending storage", async () => {
    saveChildReceipt("owner", "s", "12345678-1234-4123-8123-123456789abc", "old-parent");
    fetchRows.mockResolvedValueOnce([]);
    vi.mocked(fetchSubagentRequestStatus).mockResolvedValueOnce({ status: "recorded", result: {
      status: "completed", no_business_writes: true, advice: { summary: "旧任务的已记录建议" },
    } });
    const view = render(<SubagentCatalogPanel sessionId="s" busy={false} />);
    fireEvent.click(screen.getByText("子任务记录"));
    fireEvent.click(await screen.findByText("目录外待确认请求 · 1"));
    fireEvent.click(screen.getByText("查询原请求"));
    expect(await screen.findByText("旧任务的已记录建议")).toBeVisible();
    expect(sessionStorage.length).toBe(0);
    fetchRows.mockResolvedValueOnce([]);
    view.rerender(<SubagentCatalogPanel sessionId="another-session" busy={false} />);
    await screen.findByText(/暂无持久化子任务记录/);
    expect(screen.queryByText("旧任务的已记录建议")).not.toBeInTheDocument();
  });
  it("keeps receipt inspection available when the child is running", async () => {
    saveChildReceipt("owner", "s", "12345678-1234-4123-8123-123456789abc", "p");
    fetchRows.mockResolvedValueOnce([{ parent_id: "p", revision: 3, recorded_at: "now", continuation_available: false,
      children: [{ child_id: "a", domain: "training", status: "running" }] }]);
    render(<SubagentCatalogPanel sessionId="s" busy={false} />);
    fireEvent.click(screen.getByText("子任务记录"));
    fireEvent.click(await screen.findByText("补充咨询"));
    expect(screen.getByText("查询原请求")).toBeVisible();
    expect(screen.getByText("继续只读咨询")).toBeDisabled();
  });
  it("requires a second confirmation and retains state on server refusal", async () => {
    const row = { parent_id: "p", revision: 2, recorded_at: "now", execution_lease: { protocol: 1 }, children: [{ child_id: "one", domain: "training", status: "running" }] };
    fetchRows.mockResolvedValueOnce([row]);
    vi.mocked(reconcileSubagentCatalog).mockRejectedValueOnce(new Error("execution_busy"));
    render(<SubagentCatalogPanel sessionId="one" busy={false} />);
    fireEvent.click(screen.getByText("子任务记录"));
    fireEvent.click(await screen.findByText("核对遗留状态"));
    expect(reconcileSubagentCatalog).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("确认核对，不重跑"));
    expect(await screen.findByRole("alert")).toHaveTextContent("未重新运行任务");
    expect(reconcileSubagentCatalog).toHaveBeenCalledWith("one", row);
    expect(screen.getByText(/训练：已记录运行态/)).toBeVisible();
  });
  it("shows cancellation without guessing running records are alive", async () => {
    fetchRows.mockResolvedValueOnce([{ parent_id: "p", revision: 3, recorded_at: "now", children: [
      { child_id: "a", domain: "training", status: "running" },
      { child_id: "b", domain: "recovery", status: "failed", failure_reason: "parent_cancelled" },
    ] }]);
    render(<SubagentCatalogPanel sessionId="one" busy={false} />);
    fireEvent.click(screen.getByText("子任务记录"));
    expect(await screen.findByText(/训练：已记录运行态，存活未核对/)).toBeVisible();
    expect(screen.getByText(/恢复：未完成（父任务取消）/)).toBeVisible();
  });
  it("distinguishes an error from an empty catalog and retries", async () => {
    fetchRows.mockRejectedValueOnce(new Error("private error"));
    render(<SubagentCatalogPanel sessionId="one" busy={false} />);
    fireEvent.click(screen.getByText("子任务记录"));
    expect(await screen.findByRole("alert")).toHaveTextContent("未确认任务状态");
    expect(screen.queryByText(/private error/)).not.toBeInTheDocument();
    fetchRows.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByText("刷新记录"));
    expect(await screen.findByText(/暂无持久化子任务记录/)).toBeVisible();
  });
  it("ignores late responses from a previous session", async () => {
    let resolveOld!: (rows: import("./api").SubagentCatalog[]) => void;
    fetchRows.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const view = render(<SubagentCatalogPanel sessionId="old" busy={false} />);
    fetchRows.mockResolvedValueOnce([]);
    view.rerender(<SubagentCatalogPanel sessionId="new" busy={false} />);
    await waitFor(() => expect(fetchRows).toHaveBeenLastCalledWith("new", expect.any(AbortSignal)));
    resolveOld([{ parent_id: "old", revision: 1, recorded_at: "private-old", children: [] }]);
    await screen.findByText(/暂无持久化子任务记录/);
    expect(screen.queryByText(/private-old/)).not.toBeInTheDocument();
  });
});
