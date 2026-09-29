const STORAGE_KEY = "fitagent.pending-chat.v1";
type StoragePort = Pick<Storage, "getItem" | "setItem">;
type Entry = { identity: string; key: string };

/** Keep unacknowledged sends stable across reloads of the same browser session. */
export class ChatRequestLedger {
  constructor(
    private storage: StoragePort,
    private newKey: () => string = () => crypto.randomUUID(),
  ) {}

  private read(): Entry[] {
    const raw = this.storage.getItem(STORAGE_KEY);
    if (raw === null) return [];
    const entries: unknown = JSON.parse(raw);
    if (!Array.isArray(entries) || entries.some((item) =>
      !item || typeof item.identity !== "string" || typeof item.key !== "string" || !item.key
    )) throw new Error("Pending chat request data is invalid; it has not been overwritten.");
    const identities = entries.map((item) => item.identity);
    if (new Set(identities).size !== identities.length)
      throw new Error("Duplicate pending chat identities; refusing a new send.");
    return entries;
  }

  getOrCreate(identity: string): string {
    const entries = this.read();
    const existing = entries.find((item) => item.identity === identity);
    if (existing) return existing.key;
    const key = this.newKey();
    this.storage.setItem(STORAGE_KEY, JSON.stringify([...entries, { identity, key }]));
    return key;
  }

  complete(identity: string, key: string): void {
    const entries = this.read();
    this.storage.setItem(STORAGE_KEY, JSON.stringify(
      entries.filter((item) => item.identity !== identity || item.key !== key),
    ));
  }
}
