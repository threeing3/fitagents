import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { StreamEventsPanel } from "./StreamEventsPanel";

vi.mock("./api", () => ({ api: vi.fn() }));
beforeEach(() => { vi.mocked(api).mockReset(); sessionStorage.clear(); });
const first = { next_cursor: "journal:1", has_more: true, damaged_tail: false, status: "unconfirmed",
  events: [{ position: 1, event: { type: "step", name: "first" } }] };

it("requests subsequent positions without reloading or duplicating earlier events", async () => {
  vi.mocked(api).mockResolvedValueOnce(first).mockResolvedValueOnce({ ...first, next_cursor: "journal:2", has_more: false,
    events: [first.events[0], { position: 2, event: { type: "step", name: "second" } }] });
  render(<StreamEventsPanel runId="run-a" />);
  expect(api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("按位置读取后续事件"));
  await screen.findByText("1. first");
  fireEvent.click(screen.getByText("读取下一页"));
  await screen.findByText("2. second");
  expect(api).toHaveBeenLastCalledWith("/v1/agent-runs/run-a/events?limit=100&cursor=journal%3A1");
  expect(screen.getAllByText("1. first")).toHaveLength(1);
});

it("retains read records and cursor when a later request fails", async () => {
  vi.mocked(api).mockResolvedValueOnce(first).mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce({ ...first, events: [], has_more: false, damaged_tail: true });
  render(<StreamEventsPanel runId="run-a" />);
  fireEvent.click(screen.getByText("按位置读取后续事件"));
  await screen.findByText("1. first");
  fireEvent.click(screen.getByText("读取下一页"));
  await screen.findByRole("alert");
  expect(screen.getByText("1. first")).toBeInTheDocument();
  fireEvent.click(screen.getByText("读取下一页"));
  await screen.findByText(/位置未越过损坏部分/);
  expect(api).toHaveBeenLastCalledWith("/v1/agent-runs/run-a/events?limit=100&cursor=journal%3A1");
});

it("ignores a pending result after unmount", async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = render(<StreamEventsPanel runId="run-a" />);
  fireEvent.click(screen.getByText("按位置读取后续事件"));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  view.unmount();
  await act(async () => resolve(first));
  expect(screen.queryByText("1. first")).not.toBeInTheDocument();
});

it("resumes metadata-only position after remount without restoring cached content", async () => {
  const cursor = "12345678-1234-1234-1234-123456789abc:1";
  vi.mocked(api).mockResolvedValueOnce({ ...first, next_cursor: cursor })
    .mockResolvedValueOnce({ ...first, next_cursor: cursor, events: [], has_more: false });
  const view = render(<StreamEventsPanel runId="run-a" ownerId="owner-a" />);
  fireEvent.click(screen.getByText("按位置读取后续事件"));
  await screen.findByText("1. first");
  view.unmount();
  render(<StreamEventsPanel runId="run-a" ownerId="owner-a" />);
  fireEvent.click(screen.getByText("按位置读取后续事件"));
  await screen.findByText(/已恢复至位置 1/);
  await waitFor(() => expect(api).toHaveBeenLastCalledWith(`/v1/agent-runs/run-a/events?limit=100&cursor=${encodeURIComponent(cursor)}`));
  expect(screen.queryByText("1. first")).not.toBeInTheDocument();
  expect(sessionStorage.getItem(sessionStorage.key(0)!)!).not.toContain("events");
});

it("lets the user read from the beginning without executing business actions", async () => {
  const cursor = "12345678-1234-1234-1234-123456789abc:1";
  vi.mocked(api).mockResolvedValue({ ...first, next_cursor: cursor });
  render(<StreamEventsPanel runId="run-a" ownerId="owner-a" />);
  fireEvent.click(screen.getByText("按位置读取后续事件"));
  await screen.findByText("1. first");
  fireEvent.click(screen.getByText("从头只读查看"));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  expect(api).toHaveBeenLastCalledWith("/v1/agent-runs/run-a/events?limit=100");
});

it("ignores the previous owner's late response and bookmark after scope changes", async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockReturnValueOnce(new Promise(done => { resolve = done; }))
    .mockResolvedValue({ ...first, events: [] });
  const view = render(<StreamEventsPanel runId="run-a" ownerId="owner-a" />);
  fireEvent.click(screen.getByText("按位置读取后续事件"));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  view.rerender(<StreamEventsPanel runId="run-a" ownerId="owner-b" />);
  await act(async () => resolve(first));
  expect(screen.queryByText("1. first")).not.toBeInTheDocument();
  expect(sessionStorage.length).toBe(0);
});
