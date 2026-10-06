import { beforeEach, describe, expect, it } from "vitest";
import { listChildReceiptParents, receiptSlot, saveChildReceipt } from "./subagentRequestReceipt";

const key = "12345678-1234-4123-8123-123456789abc";
beforeEach(() => sessionStorage.clear());
describe("pending child receipt discovery", () => {
  it("discovers only valid metadata in the exact account and session", () => {
    saveChildReceipt("owner", "session", key, "old-parent");
    saveChildReceipt("owner", "session", key);
    saveChildReceipt("other-owner", "session", key, "foreign");
    saveChildReceipt("owner", "session-extra", key, "wrong-session");
    sessionStorage.setItem(receiptSlot("owner", "session", "malformed"), "not-a-request-id");
    sessionStorage.setItem(receiptSlot("owner", "session", "").concat("%"), key);
    expect(listChildReceiptParents("owner", "session")).toEqual(["old-parent"]);
    expect(listChildReceiptParents(undefined, "session")).toEqual([]);
  });
});
