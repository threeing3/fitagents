import { beforeEach, expect, it, vi } from "vitest";
import { loadTracePosition, saveTracePosition } from "./tracePosition";

const cursor = "12345678-1234-1234-1234-123456789abc:12";
beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });

it("stores metadata only and isolates owner and run", () => {
  expect(saveTracePosition("owner-a", "run-a", cursor)).toBe(true);
  expect(loadTracePosition("owner-a", "run-a")).toBe(cursor);
  expect(loadTracePosition("owner-b", "run-a")).toBeNull();
  expect(loadTracePosition("owner-a", "run-b")).toBeNull();
  expect(loadTracePosition(undefined, "run-a")).toBeNull();
  const stored = JSON.parse(sessionStorage.getItem(sessionStorage.key(0)!)!);
  expect(Object.keys(stored).sort()).toEqual(["cursor", "ownerId", "runId", "version"]);
});

it("rejects malformed metadata and ignores blocked browser storage", () => {
  saveTracePosition("owner-a", "run-a", cursor);
  const key = sessionStorage.key(0)!;
  sessionStorage.setItem(key, JSON.stringify({ version: 1, ownerId: "other", runId: "run-a", cursor }));
  expect(loadTracePosition("owner-a", "run-a")).toBeNull();
  expect(saveTracePosition("owner-a", "run-a", "../../outside:1")).toBe(false);
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  expect(loadTracePosition("owner-a", "run-a")).toBeNull();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  expect(saveTracePosition("owner-a", "run-a", cursor)).toBe(false);
});
