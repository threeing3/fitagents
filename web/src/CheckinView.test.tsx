import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { CheckinView } from "./CheckinView";
import { submitCheckin } from "./api";

vi.mock("./api", () => ({ submitCheckin: vi.fn() }));
vi.mock("./LanguageContext", () => ({ useLanguage: () => ({ language: "zh", isZh: true }) }));

beforeEach(() => { vi.clearAllMocks(); });

it("reports a saved checkin and unexecuted proposal, not an automatic plan change", async () => {
  vi.mocked(submitCheckin).mockResolvedValue({
    checkin_id: "checkin", auto_adjusted: false, idempotent_replay: false,
    adjustment_proposal: { status: "waiting_approval", approval_id: "approval" },
  });
  const notice = vi.fn();
  render(<CheckinView session={{ user_id: "owner", session_id: "session", title: "test" }}
    busy={false} setBusy={vi.fn()} setNotice={notice} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "提交打卡" }));
  await waitFor(() => expect(notice).toHaveBeenCalledWith(
    "打卡已记录，计划尚未修改。请到长期跟踪查看并确认调整草案。"));
  expect(submitCheckin).toHaveBeenCalledTimes(1);
});

it("reports stale approvals after a correction without claiming execution", async () => {
  vi.mocked(submitCheckin).mockResolvedValue({
    checkin_id: "checkin", auto_adjusted: false, idempotent_replay: false,
    invalidated_approvals: ["old"], adjustment_proposal: { status: "not_proposed" },
  });
  const notice = vi.fn();
  render(<CheckinView session={{ user_id: "owner", session_id: "session", title: "test" }}
    busy={false} setBusy={vi.fn()} setNotice={notice} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "提交打卡" }));
  await waitFor(() => expect(notice).toHaveBeenCalledWith(
    "打卡已更新，旧调整草案已失效，计划未修改。"));
});

it("does not claim an unchanged plan when a legacy server reports automatic adjustment", async () => {
  vi.mocked(submitCheckin).mockResolvedValue({
    checkin_id: "legacy", auto_adjusted: true, idempotent_replay: false,
  });
  const notice = vi.fn();
  render(<CheckinView session={{ user_id: "owner", session_id: "session" }}
    busy={false} setBusy={vi.fn()} setNotice={notice} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "提交打卡" }));
  await waitFor(() => expect(notice).toHaveBeenCalledWith(
    "打卡已记录，服务返回了旧版自动调整结果，请刷新计划核对。"));
});
