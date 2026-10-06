import { useEffect, useState } from "react";
import { FileText, MessageSquare } from "lucide-react";
import { listSessions } from "./api";
import type { SessionState } from "./types";
import { useLanguage } from "./LanguageContext";

export function SessionHistory({ session, busy, onSelect }: { session: SessionState | null; busy: boolean; onSelect: (value: SessionState) => void }) {
  const { isZh } = useLanguage();
  const [rows, setRows] = useState<SessionState[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setRows([]); setFailed(false);
    if (!session) return;
    listSessions().then(result => {
      if (active) setRows(result.filter(row => row.user_id === session.user_id));
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [session?.user_id, session?.session_id]);
  return <section className="session-history" aria-label={isZh ? "会话历史" : "Conversation history"}>
    <h2>{isZh ? "会话" : "Conversations"}</h2>
    {failed && <p role="status">{isZh ? "会话列表暂不可用" : "Conversations unavailable"}</p>}
    {rows.map(row => <button key={row.session_id} className={`session-history-item ${row.session_id === session?.session_id ? "active" : ""}`} aria-current={row.session_id === session?.session_id ? "page" : undefined} disabled={busy} onClick={() => onSelect(row)} title={row.title || (isZh ? "未命名会话" : "Untitled conversation")}>
      {row.session_id === session?.session_id ? <FileText size={20} /> : <MessageSquare size={20} />}<span>{row.title || (isZh ? "未命名会话" : "Untitled conversation")}</span>
    </button>)}
  </section>;
}
