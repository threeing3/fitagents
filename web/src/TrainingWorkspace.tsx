import { useEffect, useState } from "react";
import { CalendarDays, ListChecks } from "lucide-react";
import { WorkoutView } from "./WorkoutView";
import { ResponsibilityView } from "./ResponsibilityView";
import { useLanguage } from "./LanguageContext";
import { PlanSchedule } from "./PlanReview";
import type { Dashboard, SessionState } from "./types";

type Props = {
  session: SessionState | null;
  userId: string;
  busy: boolean;
  setBusy: (value: boolean) => void;
  setNotice: (value: string) => void;
  onRefresh: () => void;
  dashboard?: Dashboard | null;
  initialPane?: "records" | "adjustments";
};

export function TrainingWorkspace({ userId, dashboard, initialPane = "records", ...props }: Props) {
  const { isZh } = useLanguage();
  const [pane, setPane] = useState<"records" | "adjustments">(initialPane);
  const [openedAdjustments, setOpenedAdjustments] = useState(initialPane === "adjustments");
  useEffect(() => {
    setPane(initialPane);
    if (initialPane === "adjustments") setOpenedAdjustments(true);
  }, [initialPane]);
  return <div className="training-workspace">
    <header className="training-workspace-header"><div><p className="workspace-eyebrow">FitAgent</p><h2>{isZh ? "记录与安排，分清再修改" : "Records and plans, with clear changes"}</h2></div>
      <div role="tablist" aria-label={isZh ? "训练工作区" : "Training workspace"} onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? "records" : event.key === "End" ? "adjustments" : pane === "records" ? "adjustments" : "records";
        if (next === "adjustments") setOpenedAdjustments(true);
        setPane(next);
        document.getElementById(next === "records" ? "training-record-tab" : "training-adjust-tab")?.focus();
      }}>
        <button id="training-record-tab" role="tab" tabIndex={pane === "records" ? 0 : -1} aria-selected={pane === "records"} aria-controls="training-record-panel" onClick={() => setPane("records")}><ListChecks size={16} />{isZh ? "训练记录" : "Workout records"}</button>
        <button id="training-adjust-tab" role="tab" tabIndex={pane === "adjustments" ? 0 : -1} aria-selected={pane === "adjustments"} aria-controls="training-adjust-panel" onClick={() => { setOpenedAdjustments(true); setPane("adjustments"); }}><CalendarDays size={16} />{isZh ? "计划与调整" : "Plan adjustments"}</button>
      </div>
    </header>
    <div id="training-record-panel" role="tabpanel" aria-labelledby="training-record-tab" hidden={pane !== "records"}><WorkoutView {...props} /></div>
    {openedAdjustments && <div id="training-adjust-panel" role="tabpanel" aria-labelledby="training-adjust-tab" hidden={pane !== "adjustments"}><div className="plan-review-workspace"><PlanSchedule dashboard={dashboard} onRefresh={props.onRefresh} /><ResponsibilityView userId={userId} /></div></div>}
  </div>;
}
