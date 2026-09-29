import { describe, expect, it } from "vitest";
import { ChatRequestLedger } from "./chatRequestLedger";

function fixture() {
  const data = new Map<string, string>();
  const storage = {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
  };
  let sequence = 0;
  return { data, storage, fresh: () => new ChatRequestLedger(storage, () => `request-${++sequence}`) };
}

describe("pending chat request identity", () => {
  it("reuses an unacknowledged identity after recreating the page ledger", () => {
    const { fresh } = fixture();
    const key = fresh().getOrCreate('["user","session","record workout"]');
    expect(fresh().getOrCreate('["user","session","record workout"]')).toBe(key);
  });

  it("separates users, conversations and message content", () => {
    const { fresh } = fixture();
    const ledger = fresh();
    const identities = [["u1", "s1", "m1"], ["u2", "s1", "m1"], ["u1", "s2", "m1"], ["u1", "s1", "m2"]];
    expect(new Set(identities.map((id) => ledger.getOrCreate(JSON.stringify(id)))).size).toBe(4);
  });

  it("allows a new intentional send only after acknowledgement", () => {
    const { fresh } = fixture();
    const oldKey = fresh().getOrCreate("same message");
    fresh().complete("same message", oldKey);
    expect(fresh().getOrCreate("same message")).not.toBe(oldKey);
  });

  it("does not let a delayed acknowledgement remove a newer request", () => {
    const { fresh } = fixture();
    const key = fresh().getOrCreate("message");
    fresh().complete("message", key);
    const newer = fresh().getOrCreate("message");
    fresh().complete("message", key);
    expect(fresh().getOrCreate("message")).toBe(newer);
  });

  it("preserves corrupted state and unrelated storage instead of resetting them", () => {
    const { data, fresh } = fixture();
    data.set("fitagent.pending-chat.v1", "not-json");
    data.set("other-app", "preserve");
    expect(() => fresh().getOrCreate("message")).toThrow();
    expect(data.get("fitagent.pending-chat.v1")).toBe("not-json");
    expect(data.get("other-app")).toBe("preserve");
  });

  it("does not return an unsaved new key when storage fails", () => {
    const ledger = new ChatRequestLedger({ getItem: () => null, setItem: () => { throw new Error("quota"); } });
    expect(() => ledger.getOrCreate("message")).toThrow("quota");
  });
});
