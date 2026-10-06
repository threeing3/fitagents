import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ResponsibilityView, activityLabel } from "./ResponsibilityView";
import { createResponsibilityDemo } from "./responsibilityDemo";
import type { ApprovalActivity } from "./types";

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });

describe("long-term responsibility view", () => {
  it("requires explicit interruption reconciliation and never requeues", async () => {
    const demo = createResponsibilityDemo();
    const history = await demo.gateway.history();
    history[0].status = "approved";
    history[0].job_id = "interrupted-job";
    history[0].job_status = "running";
    history[0].job_attempts = 1;
    const reconcile = vi.fn(async () => {
      history[0].status = "failed";
      history[0].job_status = "failed";
      return { status: "failed", changed: true, requeued: false };
    });
    render(<ResponsibilityView userId="demo" gateway={{ ...demo.gateway, history: async () => history, reconcile }} />);
    fireEvent.click(await screen.findByRole("button", { name: "核对中断执行" }));
    expect(reconcile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认核对，不重跑" }));
    await screen.findByText("执行失败");
    expect(reconcile).toHaveBeenCalledTimes(1);
    expect(reconcile).toHaveBeenCalledWith("interrupted-job", 1);
  });
  it("requires a single scoped confirmation, then distinguishes approval from execution", async () => {
    const demo = createResponsibilityDemo();
    const decide = vi.spyOn(demo.gateway, "decide");
    render(<ResponsibilityView userId="demo" gateway={demo.gateway} demoAdvance={demo.advance} />);
    await screen.findByRole("button", { name: "审阅并批准" });
    fireEvent.click(screen.getByRole("button", { name: "审阅并批准" }));
    expect(decide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认批准一次" }));
    fireEvent.click(screen.getByRole("button", { name: "确认批准一次" }));
    await screen.findByText("已批准 · 等待后台执行");
    expect(decide).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("已执行 · 计划校验通过")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "推进演示后台" }));
    await screen.findByText("已执行 · 计划校验通过");
  });

  it("denies without executing and creates an explicit bounded schedule", async () => {
    const demo = createResponsibilityDemo();
    const create = vi.spyOn(demo.gateway, "create");
    render(<ResponsibilityView userId="demo" gateway={demo.gateway} />);
    await screen.findByRole("button", { name: "拒绝" });
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await screen.findByText("已拒绝");
    fireEvent.change(screen.getByRole("spinbutton", { name: "持续周数" }), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "创建责任" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ weeks: 6, weekday: 6, hour: 18, minute: 0 })));
    await screen.findByText("责任已创建；按账户时区执行复盘。");
  });

  it("preserves an uncertain write across reload and does not auto-retry", async () => {
    const demo = createResponsibilityDemo();
    const decide = vi.spyOn(demo.gateway, "decide").mockRejectedValue(new Error("response lost"));
    const first = render(<ResponsibilityView userId="owner" gateway={demo.gateway} />);
    await screen.findByRole("button", { name: "拒绝" });
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await screen.findByText(/上次操作结果未确认/);
    expect(sessionStorage.getItem("fitagent.responsibility-uncertain.owner")).toBe("1");
    expect(screen.getByRole("button", { name: "创建责任" })).toBeDisabled();
    first.unmount();
    render(<ResponsibilityView userId="owner" gateway={demo.gateway} />);
    await screen.findByText(/上次操作结果未确认/);
    expect(decide).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
  });

  it("clears records on account unmount and ignores delayed old reads", async () => {
    const oldDemo = createResponsibilityDemo();
    let resolve!: (value: Awaited<ReturnType<typeof oldDemo.gateway.list>>) => void;
    oldDemo.gateway.list = () => new Promise(done => { resolve = done; });
    const first = render(<ResponsibilityView userId="old" gateway={oldDemo.gateway} />);
    first.unmount();
    const empty = createResponsibilityDemo();
    empty.gateway.list = async () => [];
    empty.gateway.history = async () => [];
    render(<ResponsibilityView userId="new" gateway={empty.gateway} />);
    await screen.findByText("暂无长期责任，可以先创建一个四周复盘。");
    resolve([]);
    expect(screen.queryByText("demo-approval")).not.toBeInTheDocument();
  });

  it("reports failed reads as errors rather than confirmed empty data", async () => {
    const demo = createResponsibilityDemo();
    demo.gateway.history = vi.fn().mockRejectedValue(new Error("unavailable"));
    render(<ResponsibilityView userId="owner" gateway={demo.gateway} />);
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "创建责任" })).toBeDisabled();
    expect(screen.queryByText("暂无长期责任，可以先创建一个四周复盘。")).not.toBeInTheDocument();
  });

  it("never calls a skipped or unverifiable job a verified execution", () => {
    const row = { status: "executed", job_status: "completed", result: { status: "skipped", verified: true } } as ApprovalActivity;
    expect(activityLabel(row, true)).toBe("执行确认不完整 · 需核对");
    expect(activityLabel({ ...row, status: "outcome_unknown" }, true)).toContain("需人工核对");
  });
});
