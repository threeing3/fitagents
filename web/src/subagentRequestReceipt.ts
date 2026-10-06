// Metadata only. Server authentication remains authoritative for receipt access.
export function receiptSlot(ownerId: string, sessionId: string, parentId?: string) {
  return `fitagent:child-receipt:v1:${encodeURIComponent(ownerId)}:${encodeURIComponent(sessionId)}:${encodeURIComponent(parentId ?? "start")}`;
}
export function readChildReceipt(ownerId: string | undefined, sessionId: string, parentId?: string): string | null {
  if (!ownerId) return null;
  try {
    const value = sessionStorage.getItem(receiptSlot(ownerId, sessionId, parentId));
    return value && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value) ? value : null;
  } catch { return null; }
}
export function saveChildReceipt(ownerId: string, sessionId: string, key: string, parentId?: string) {
  // Fail closed before model execution when browser storage is unavailable.
  sessionStorage.setItem(receiptSlot(ownerId, sessionId, parentId), key);
}
export function clearChildReceipt(ownerId: string, sessionId: string, parentId?: string) {
  sessionStorage.removeItem(receiptSlot(ownerId, sessionId, parentId));
}

export function listChildReceiptParents(ownerId: string | undefined, sessionId: string): string[] {
  if (!ownerId) return [];
  const prefix = receiptSlot(ownerId, sessionId).slice(0, -"start".length);
  const parents: string[] = [];
  try {
    for (let index = 0; index < sessionStorage.length; index++) {
      const slot = sessionStorage.key(index);
      if (!slot?.startsWith(prefix)) continue;
      const suffix = slot.slice(prefix.length);
      if (!suffix || suffix === "start") continue;
      try {
        const parent = decodeURIComponent(suffix);
        if (parent.length <= 128 && readChildReceipt(ownerId, sessionId, parent)) parents.push(parent);
      } catch { /* Ignore malformed metadata, not an authenticated receipt. */ }
    }
  } catch { return []; }
  return [...new Set(parents)].sort();
}
