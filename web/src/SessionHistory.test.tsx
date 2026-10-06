import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { listSessions } from "./api";
import { SessionHistory } from "./SessionHistory";

vi.mock("./api", () => ({ listSessions: vi.fn() }));
afterEach(() => vi.clearAllMocks());
const session = { user_id: "mine", session_id: "first", title: "本周训练调整" };

it("lists owned conversations only and selects an explicit session", async () => {
  vi.mocked(listSessions).mockResolvedValue([
    { ...session, created_at: "2026-10-03" },
    { user_id: "other", session_id: "private", title: "其他账号", created_at: "2026-10-03" },
    { user_id: "mine", session_id: "second", title: "跑步安排", created_at: "2026-10-03" },
  ]);
  const select = vi.fn();
  render(<SessionHistory session={session} busy={false} onSelect={select} />);
  const button = await screen.findByRole("button", { name: "跑步安排" });
  expect(screen.queryByRole("button", { name: "其他账号" })).toBeNull();
  expect(screen.getByRole("button", { name: "本周训练调整" })).toHaveAttribute("aria-current", "page");
  fireEvent.click(button);
  expect(select).toHaveBeenCalledWith(expect.objectContaining({ session_id: "second", user_id: "mine" }));
});

it("disables session switches while a response is in flight", async () => {
  vi.mocked(listSessions).mockResolvedValue([{ ...session, created_at: "2026-10-03" }]);
  const select = vi.fn();
  render(<SessionHistory session={session} busy onSelect={select} />);
  const button = await screen.findByRole("button", { name: "本周训练调整" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(select).not.toHaveBeenCalled();
});

it("ignores a previous owner's late response", async () => {
  let resolve!: (rows: Awaited<ReturnType<typeof listSessions>>) => void;
  vi.mocked(listSessions).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  vi.mocked(listSessions).mockResolvedValueOnce([{ user_id: "next", session_id: "next-session", title: "新账号会话", created_at: "2026-10-03" }]);
  const { rerender } = render(<SessionHistory session={session} busy={false} onSelect={vi.fn()} />);
  rerender(<SessionHistory session={{ user_id: "next", session_id: "next-session" }} busy={false} onSelect={vi.fn()} />);
  await screen.findByRole("button", { name: "新账号会话" });
  resolve([{ ...session, created_at: "2026-10-03" }]);
  expect(screen.queryByRole("button", { name: "本周训练调整" })).toBeNull();
});
