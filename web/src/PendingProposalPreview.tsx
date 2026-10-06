import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";
import { responsibilityApi } from "./api";
import { ProposalDiff } from "./PlanReview";
import { ExecutionTimeline } from "./ExecutionTimeline";
import { useLanguage } from "./LanguageContext";
import type { ApprovalActivity } from "./types";

export function PendingProposalPreview({ userId, refreshKey, onReview, onViewPlan }: { userId?: string; refreshKey?: number; onReview: () => void; onViewPlan?: () => void }) {
  const { isZh } = useLanguage();
  const [items, setItems] = useState<ApprovalActivity[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    setItems([]); setError(false);
    if (!userId) return;
    responsibilityApi.history().then(rows => {
      if (active) setItems(rows.filter(row => row.status === "pending" && row.tool_name === "plan.reduce_sets"));
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [userId, refreshKey]);
  if (error) return <p className="pending-read-error" role="status">{isZh ? "待审阅状态暂未读取，前往长期跟踪刷新核对。" : "Pending status unavailable. Refresh in ongoing goals."}</p>;
  if (!items.length) return null;
  const item = items[0];
  return <section className="pending-proposal-preview" aria-label={isZh ? "有调整待你审阅" : "Change awaiting your review"}>
    <ExecutionTimeline events={item.context.execution_events} />
    <ProposalDiff item={item} />
    <p className="proposal-review-note">{isZh ? "你可以审阅这份候选方案，决定是否采用。在你明确批准前，原计划保持不变。" : "Review this proposal and decide whether to use it. Your original plan remains unchanged until you approve."}</p>
    <button className="pill-btn" onClick={onReview}>{isZh ? "审阅调整" : "Review changes"}<ArrowRight size={16} /></button>
    {onViewPlan && <button className="pill-btn proposal-original-btn" onClick={onViewPlan}>{isZh ? "查看原计划" : "View original plan"}</button>}
    {items.length > 1 && <small>{isZh ? `另有 ${items.length - 1} 份待审阅草案` : `${items.length - 1} more proposals`}</small>}
  </section>;
}
