import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useLanguage } from "./LanguageContext";
import { loadTracePosition, saveTracePosition } from "./tracePosition";

type Page = {
  next_cursor: string; has_more: boolean; damaged_tail: boolean; status: string;
  events: Array<{ position: number; event: Record<string, unknown> }>;
};

export function StreamEventsPanel({ runId, ownerId }: { runId: string; ownerId?: string }) {
  const { isZh } = useLanguage();
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [events, setEvents] = useState<Page["events"]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [restored, setRestored] = useState<string | null>(null);
  const [saveFailed, setSaveFailed] = useState(false);
  const cursor = useRef<string | null>(null);
  useEffect(() => {
    const saved = loadTracePosition(ownerId, runId);
    cursor.current = saved; setRestored(saved); setPage(null); setEvents([]); setOpen(false); setError(false); setSaveFailed(false);
  }, [runId, ownerId]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true); setError(false);
    const after = cursor.current ? `&cursor=${encodeURIComponent(cursor.current)}` : "";
    api<Page>(`/v1/agent-runs/${encodeURIComponent(runId)}/events?limit=100${after}`)
      .then(value => {
        if (!active) return;
        cursor.current = value.next_cursor; setPage(value);
        if (ownerId) setSaveFailed(!saveTracePosition(ownerId, runId, value.next_cursor));
        setEvents(previous => Array.from(new Map([...previous, ...value.events].map(event => [event.position, event])).values()));
      })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, attempt, runId, ownerId]);
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{isZh ? "按位置读取后续事件" : "Read subsequent stream events"}</summary>
    {open && <>
      <p>{isZh ? "仅追加日志的原始顺序，不是合并轨迹的全局顺序；不会重新执行。" : "Append-journal order only, not global trace order. No execution."}</p>
      {restored && <p>{isZh ? `已恢复至位置 ${restored.split(":")[1]}，此处仅显示后续记录；完整历史仍可在上方查看。` : "Reading position restored; subsequent records only. Full history remains above."}</p>}
      {saveFailed && <p role="status">{isZh ? "浏览器未能保存读取位置；当前读取不受影响，刷新后可能从头读取。" : "Position could not be saved; current reading is unaffected."}</p>}
      <button disabled={loading} onClick={() => setAttempt(value => value + 1)}>{isZh ? (page?.has_more ? "读取下一页" : "检查后续记录") : "Read next events"}</button>
      <button disabled={loading} onClick={() => {
        cursor.current = null; setRestored(null); setEvents([]); setPage(null);
        if (ownerId) setSaveFailed(!saveTracePosition(ownerId, runId, null));
        setAttempt(value => value + 1);
      }}>{isZh ? "从头只读查看" : "Read from beginning"}</button>
      {loading && <p role="status">{isZh ? "正在读取…" : "Loading…"}</p>}
      {error && <p role="alert">{isZh ? "增量日志暂不可读或位置无效；已读取的记录保留，没有重跑。" : "Stream unavailable or cursor invalid; existing records retained, no retry execution."}</p>}
      {page?.damaged_tail && <p role="alert">{isZh ? "尾部不完整，仅保留完整记录；位置未越过损坏部分。" : "Incomplete tail; cursor stays before the damaged portion."}</p>}
      {page && <p>{isZh ? "日志状态" : "Journal state"}：{page.status} · {events.length} {isZh ? "条已读取；未核对进程存活。" : "records; process liveness not checked."}</p>}
      <ol>{events.map(item => <li key={item.position}><details>
        <summary>{item.position}. {String(item.event.name || item.event.type || "event")}</summary>
        <pre>{JSON.stringify(item.event, null, 2)}</pre>
      </details></li>)}</ol>
    </>}
  </details>;
}
