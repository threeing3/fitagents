import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cancelSubagentRequest, fetchSubagentRequestStatus, reconcileSubagentRequest, requestSubagentTurn } from "./api";
import { SubagentTurnForm } from "./SubagentTurnForm";
import { readChildReceipt } from "./subagentRequestReceipt";
import { saveChildReceipt } from "./subagentRequestReceipt";

vi.mock("./api", () => ({ requestSubagentTurn: vi.fn(), fetchSubagentRequestStatus: vi.fn(), cancelSubagentRequest: vi.fn(), reconcileSubagentRequest: vi.fn() }));
vi.mock("./AuthContext", () => ({ useAuth: () => ({ user: { user_id: "owner" } }) }));
beforeEach(() => { sessionStorage.clear(); vi.resetAllMocks(); });
describe("read-only child turn", () => {
  it("requires two-step confirmation to close an orphan receipt at the exact revision", async () => {
    saveChildReceipt("owner", "s", "12345678-1234-4123-8123-123456789abc", "parent");
    vi.mocked(reconcileSubagentRequest).mockResolvedValueOnce({ status: "recorded", result: { status: "failed", failure_reason: "execution_interrupted", no_business_writes: true } });
    const row = { parent_id: "parent", revision: 9, recorded_at: "now", execution_lease: { protocol: 1 }, children: [{ child_id: "one", domain: "training", status: "failed" }] };
    render(<SubagentTurnForm sessionId="s" busy={false} row={row} onRecorded={vi.fn()} />);
    fireEvent.click(screen.getByText("核对请求回执"));
    expect(reconcileSubagentRequest).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("确认关闭孤儿回执"));
    await screen.findByText("未完成咨询或已被安全规则拦截");
    expect(reconcileSubagentRequest).toHaveBeenCalledWith("s", "12345678-1234-4123-8123-123456789abc", 9);
    expect(requestSubagentTurn).not.toHaveBeenCalled();
  });
  it("requires confirmation to request stop and never claims it is already stopped", async () => {
    vi.mocked(requestSubagentTurn).mockRejectedValueOnce(new Error("disconnect"));
    vi.mocked(cancelSubagentRequest).mockResolvedValueOnce({ status: "cancel_requested", no_automatic_retry: true });
    render(<SubagentTurnForm sessionId="s" busy={false} onRecorded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("子任务问题"), { target: { value: "训练建议" } });
    fireEvent.click(screen.getByText("启动只读咨询"));
    await screen.findByText("查询原请求");
    fireEvent.click(screen.getByText("请求停止"));
    expect(cancelSubagentRequest).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("确认停止，不重跑"));
    await screen.findByText("已收到停止请求；是否已停止，以最终回执为准。");
    expect(cancelSubagentRequest).toHaveBeenCalledWith("s", vi.mocked(requestSubagentTurn).mock.calls.at(-1)![1]);
    expect(screen.getByText("启动只读咨询")).toBeDisabled();
  });
  it("queries the same receipt after a transport failure without resubmitting", async () => {
    vi.mocked(requestSubagentTurn).mockRejectedValueOnce(new Error("private transport detail"));
    vi.mocked(fetchSubagentRequestStatus).mockResolvedValueOnce({ status: "unconfirmed" });
    vi.mocked(fetchSubagentRequestStatus).mockResolvedValueOnce({ status: "recorded", result: {
      status: "completed", no_business_writes: true, advice: { summary: "合成建议" },
    } });
    const recorded = vi.fn();
    render(<SubagentTurnForm sessionId="s" busy={false} onRecorded={recorded} />);
    fireEvent.change(screen.getByLabelText("子任务问题"), { target: { value: "解释恢复建议" } });
    fireEvent.click(screen.getByText("启动只读咨询"));
    await screen.findByText("查询原请求");
    expect(screen.getByText("启动只读咨询")).toBeDisabled();
    const key = vi.mocked(requestSubagentTurn).mock.calls.at(-1)![1];
    fireEvent.click(screen.getByText("查询原请求"));
    await waitFor(() => expect(fetchSubagentRequestStatus).toHaveBeenCalledWith("s", key));
    await waitFor(() => expect(screen.getByText("查询原请求")).not.toBeDisabled());
    fireEvent.click(screen.getByText("查询原请求"));
    await screen.findByText("合成建议");
    expect(requestSubagentTurn).toHaveBeenCalledTimes(1);
    expect(recorded).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("private transport detail")).not.toBeInTheDocument();
  });
  it("passes the exact catalog revision for explicit continuation", async () => {
    vi.mocked(requestSubagentTurn).mockResolvedValueOnce({ status: "failed", no_business_writes: true });
    const row = { parent_id: "parent", revision: 8, recorded_at: "now", children: [], continuation_available: true };
    render(<SubagentTurnForm sessionId="s" busy={false} row={row} onRecorded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("子任务问题"), { target: { value: "补充解释" } });
    fireEvent.click(screen.getByText("继续只读咨询"));
    await screen.findByText("未完成咨询或已被安全规则拦截");
    expect(requestSubagentTurn).toHaveBeenLastCalledWith("s", expect.any(String), "补充解释", "training", row);
  });
  it("restores an unconfirmed continuation after unmount even when the child is running", async () => {
    vi.mocked(requestSubagentTurn).mockRejectedValueOnce(new Error("disconnect"));
    const row = { parent_id: "p", revision: 1, recorded_at: "now", children: [], continuation_available: true };
    const view = render(<SubagentTurnForm sessionId="s" busy={false} row={row} onRecorded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("子任务问题"), { target: { value: "不要存储这个问题" } });
    fireEvent.click(screen.getByText("继续只读咨询"));
    await screen.findByText("查询原请求");
    const key = readChildReceipt("owner", "s", "p");
    expect(key).toBeTruthy();
    expect(readChildReceipt("other-owner", "s", "p")).toBeNull();
    expect(readChildReceipt("owner", "other-session", "p")).toBeNull();
    expect(sessionStorage.getItem(sessionStorage.key(0)!)).not.toContain("不要存储");
    view.unmount();
    vi.mocked(fetchSubagentRequestStatus).mockResolvedValueOnce({ status: "recorded", result: { status: "completed", no_business_writes: true } });
    render(<SubagentTurnForm sessionId="s" busy={false} row={{ ...row, revision: 2, continuation_available: false }} onRecorded={vi.fn()} />);
    fireEvent.click(screen.getByText("查询原请求"));
    await screen.findByText("建议已返回，未执行变更");
    expect(fetchSubagentRequestStatus).toHaveBeenLastCalledWith("s", key);
    expect(readChildReceipt("owner", "s", "p")).toBeNull();
  });
  it("does not send when storing the receipt key fails", async () => {
    const store = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage unavailable"); });
    const before = vi.mocked(requestSubagentTurn).mock.calls.length;
    try {
      render(<SubagentTurnForm sessionId="s" busy={false} onRecorded={vi.fn()} />);
      fireEvent.change(screen.getByLabelText("子任务问题"), { target: { value: "训练建议" } });
      fireEvent.click(screen.getByText("启动只读咨询"));
      await screen.findByText("无法保存请求号，尚未发送咨询。请检查浏览器存储权限。");
      expect(requestSubagentTurn).toHaveBeenCalledTimes(before);
    } finally { store.mockRestore(); }
  });
});
