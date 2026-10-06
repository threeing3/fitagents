const prefix = "fitagent:trace-position:v1:";
const cursorPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9]+$/;

function key(ownerId: string, runId: string) {
  return `${prefix}${encodeURIComponent(ownerId)}:${encodeURIComponent(runId)}`;
}

export function loadTracePosition(ownerId: string | undefined, runId: string): string | null {
  if (!ownerId) return null;
  try {
    const raw = sessionStorage.getItem(key(ownerId, runId));
    if (!raw || raw.length > 512) return null;
    const value = JSON.parse(raw);
    if (value.version !== 1 || value.ownerId !== ownerId || value.runId !== runId ||
      typeof value.cursor !== "string" || !cursorPattern.test(value.cursor)) return null;
    const position = Number(value.cursor.split(":")[1]);
    return Number.isSafeInteger(position) && position >= 0 ? value.cursor : null;
  } catch { return null; }
}

export function saveTracePosition(ownerId: string | undefined, runId: string, cursor: string | null): boolean {
  if (!ownerId) return false;
  if (cursor !== null && (!cursorPattern.test(cursor) || !Number.isSafeInteger(Number(cursor.split(":")[1])))) return false;
  try {
    // Metadata only: never copy page events, health content, or credentials.
    sessionStorage.setItem(key(ownerId, runId), JSON.stringify({ version: 1, ownerId, runId, cursor }));
    return true;
  } catch { return false; }
}
