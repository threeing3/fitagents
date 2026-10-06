import { useEffect, useRef, useState } from "react";
import { followupApi, type DecisionFollowup } from "./api";
import { useLanguage } from "./LanguageContext";

export function FollowupPanel({ refreshKey }: { refreshKey: number }) {
  const { isZh } = useLanguage();
  const [items, setItems] = useState<DecisionFollowup[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const epoch = useRef(0);
  useEffect(() => {
    const generation = ++epoch.current;
    let active = true;
    followupApi.list().then((result) => {
      if (active && generation === epoch.current) {
        if (!Array.isArray(result)) throw new Error("Invalid follow-up response");
        setItems(result.filter((item) => item.status === "pending"));
        setError("");
      }
    }).catch(() => {
      if (active && generation === epoch.current) setError(isZh ? "跟进信息暂不可用" : "Follow-ups unavailable");
    });
    return () => { active = false; };
  }, [refreshKey, isZh]);

  async function decline(item: DecisionFollowup) {
    ++epoch.current;
    setBusy(true);
    setError("");
    try {
      const result = await followupApi.decline(item.id);
      if (result.status !== "declined") throw new Error("Decline not confirmed");
      ++epoch.current;
      setItems((current) => current.filter((value) => value.evaluation_plan_id !== item.evaluation_plan_id));
      setConfirmed(true);
    } catch {
      setError(isZh ? "未确认停止，请重试；当前跟进仍保留" : "Stop not confirmed; follow-up is retained. Retry.");
    } finally {
      setBusy(false);
    }
  }

  if (!items.length && !error && !confirmed) return null;
  return <section className="followup-panel" aria-label={isZh ? "效果跟进" : "Outcome follow-ups"}>
    {items.map((item) => <div key={item.id}>
      <p>{item.question.question || item.question.text || (isZh ? "这项建议实际执行得怎么样？" : "How did this recommendation work for you?")}</p>
      <button type="button" className="chip" disabled={busy} onClick={() => void decline(item)}>
        {isZh ? "不再追问这件事" : "Stop questions about this item"}
      </button>
    </div>)}
    {confirmed && <p role="status">{isZh ? "已停止这项跟进的追问；记录和风险提示保留" : "Questions stopped for this item. Records and risk notices remain."}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
