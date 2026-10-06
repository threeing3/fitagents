import { useEffect, useRef, useState } from "react";
import { workoutCorrectionApi, type SavedWorkout, type WorkoutCorrection, type WorkoutFacts } from "./api";
import { useLanguage } from "./LanguageContext";

type Pending = { record: SavedWorkout; payload: WorkoutCorrection };
type Props = { userId: string; refreshKey: number; onRefresh: () => void };
const storageKey = (id: string) => `fitagent_pending_workout_correction_${id}`;

function readPending(id: string): Pending | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(id)) || "null") as Pending | null;
    return value?.record?.id && value.payload?.idempotency_key && value.payload.expected_revision >= 0
      ? value : null;
  } catch { return null; }
}

export function WorkoutCorrectionPanel({ userId, refreshKey, onRefresh }: Props) {
  const { isZh } = useLanguage();
  const [records, setRecords] = useState<SavedWorkout[]>([]);
  const [selected, setSelected] = useState<SavedWorkout | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [duration, setDuration] = useState("");
  const [rpe, setRpe] = useState("");
  const [completion, setCompletion] = useState("");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const owner = useRef(userId);
  owner.current = userId;

  useEffect(() => {
    owner.current = userId;
    return () => { owner.current = ""; };
  }, [userId]);

  function showFacts(facts: Partial<WorkoutFacts>) {
    const labels = {
      duration_minutes: isZh ? "时长（分钟）" : "Duration (minutes)",
      rpe: isZh ? "整体主观用力程度" : "Overall effort",
      completion_rate: isZh ? "完成度" : "Completion",
    };
    return (Object.keys(facts) as Array<keyof WorkoutFacts>).map((key) => {
      const value = facts[key];
      const text = value === null ? (isZh ? "未记录" : "Unrecorded")
        : key === "completion_rate" ? `${Number(value) * 100}%` : String(value);
      return `${labels[key]}：${text}`;
    }).join("；");
  }

  useEffect(() => {
    setRecords([]); setSelected(null); setNotice(""); setBusy(false);
    setPending(readPending(userId));
  }, [userId]);

  useEffect(() => {
    let cancelled = false;
    workoutCorrectionApi.list().then((items) => {
      if (!cancelled) setRecords(items);
    }).catch((error: Error) => {
      if (!cancelled) setNotice(`${isZh ? "读取失败" : "Load failed"}: ${error.message}`);
    });
    return () => { cancelled = true; };
  }, [userId, refreshKey, reload, isZh]);

  function selectRecord(record: SavedWorkout) {
    setSelected(record); setReason(""); setNotice("");
    setDuration(record.duration_minutes === null ? "" : String(record.duration_minutes));
    setRpe(record.rpe === null ? "" : String(record.rpe));
    setCompletion(record.completion_rate === null ? "" : String(record.completion_rate * 100));
  }

  async function submit(request: Pending) {
    const submittedOwner = userId;
    setBusy(true); setNotice("");
    try {
      const result = await workoutCorrectionApi.correct(request.record.id, request.payload);
      sessionStorage.removeItem(storageKey(submittedOwner));
      if (owner.current !== submittedOwner) return;
      setPending(null); setSelected(null); setReload((value) => value + 1); onRefresh();
      setNotice(isZh
        ? `更正已确认，版本 ${result.revision}，审计编号 ${result.audit_id}${result.idempotent_replay ? "（恢复原结果，未重复更正）" : ""}。`
        : `Correction confirmed, revision ${result.revision}, audit ${result.audit_id}${result.idempotent_replay ? " (original result recovered)" : ""}.`);
    } catch (error) {
      if (owner.current !== submittedOwner) return;
      const failure = error as Error & { status?: number };
      if ([404, 409, 422].includes(failure.status || 0)) {
        sessionStorage.removeItem(storageKey(submittedOwner));
        setPending(null); setSelected(null); setReload((value) => value + 1);
        setNotice(`${isZh ? "更正被拒绝，请重新选择并核对当前记录" : "Correction rejected; reload and verify the current record"}: ${failure.message}`);
      } else {
        setNotice(`${isZh ? "结果未确认，请重试原更正；不要重新提交新请求" : "Outcome unconfirmed; retry the original correction"}: ${failure.message}`);
      }
    } finally {
      if (owner.current === submittedOwner) setBusy(false);
    }
  }

  function confirm() {
    if (!selected || selected.revision === null || pending || busy) return;
    const facts: WorkoutFacts = {
      duration_minutes: duration.trim() ? Number(duration) : null,
      rpe: rpe.trim() ? Number(rpe) : null,
      completion_rate: completion.trim() ? Number(completion) / 100 : null,
    };
    const valid = (facts.duration_minutes === null || (Number.isInteger(facts.duration_minutes) && facts.duration_minutes >= 1 && facts.duration_minutes <= 1440))
      && (facts.rpe === null || (Number.isInteger(facts.rpe) && facts.rpe >= 1 && facts.rpe <= 10))
      && (facts.completion_rate === null || (Number.isFinite(facts.completion_rate) && facts.completion_rate >= 0 && facts.completion_rate <= 1));
    const expected: Partial<WorkoutFacts> = {}, changes: Partial<WorkoutFacts> = {};
    for (const key of Object.keys(facts) as Array<keyof WorkoutFacts>) {
      if (facts[key] !== selected[key]) { expected[key] = selected[key]; changes[key] = facts[key]; }
    }
    if (!valid || !reason.trim() || !Object.keys(changes).length) {
      setNotice(isZh ? "请填写有效的新值与更正原因；没有变化不能提交。" : "Provide valid changed facts and a reason.");
      return;
    }
    const request = { record: selected, payload: {
      idempotency_key: crypto.randomUUID(), expected_revision: selected.revision,
      expected, changes, reason: reason.trim(),
    } };
    try { sessionStorage.setItem(storageKey(userId), JSON.stringify(request)); }
    catch { setNotice(isZh ? "无法保存恢复请求，尚未提交。" : "Recovery storage unavailable; not submitted."); return; }
    setPending(request); void submit(request);
  }

  return <section className="exercise-editor workout-correction" aria-label={isZh ? "更正已保存训练" : "Correct saved workout"}>
    <h3>{isZh ? "更正已保存训练" : "Correct saved workout"}</h3>
    <p>{isZh ? "只更正时长、整体强度及完成度，不改逐组动作。留空表示明确清除该事实。原值和修订原因会保留。最近30条记录如下。" : "Correct duration, overall effort and completion, not individual sets. A blank value explicitly clears that fact. Original values and reason are retained. Latest 30 records:"}</p>
    {notice && <p role="status">{notice}</p>}
    {pending ? <div role="alert">
      <p>{isZh ? "原更正结果待确认，已冻结" : "Original correction awaiting confirmation"}: {pending.record.workout_name} · {pending.record.id}</p>
      <p>{showFacts(pending.payload.expected)} → {showFacts(pending.payload.changes)}</p>
      <p>{isZh ? "原因" : "Reason"}: {pending.payload.reason}</p>
      <button disabled={busy} onClick={() => void submit(pending)}>{isZh ? "重试原更正" : "Retry original correction"}</button>
    </div> : <>
      <button disabled={busy} onClick={() => { setSelected(null); setReload((value) => value + 1); }}>{isZh ? "刷新训练记录" : "Refresh workouts"}</button>
      <ul>{records.map((record) => <li key={record.id}>
        <span>{record.workout_name} · {new Date(record.performed_at).toLocaleString(isZh ? "zh-CN" : "en-US")} · {record.id}</span>
        <span> · {record.duration_minutes ?? "—"} min · RPE {record.rpe ?? "—"} · {record.completion_rate === null ? "—" : `${record.completion_rate * 100}%`} · {isZh ? "版本" : "revision"} {record.revision ?? "—"}</span>
        <button disabled={busy || !record.correction_available} onClick={() => selectRecord(record)} aria-label={`${isZh ? "选择更正" : "Correct"} ${record.id}`}>{isZh ? "选择更正" : "Correct"}</button>
        {!record.correction_available && <span>{isZh ? " 历史来源未核对，暂不可更正" : " Historical source unverified"}</span>}
      </li>)}</ul>
      {selected && <fieldset disabled={busy}>
        <legend>{isZh ? "核对后确认更正" : "Verify and confirm correction"}: {selected.workout_name} · {selected.id}</legend>
        <p>{isZh ? "原值" : "Original"}: {showFacts({ duration_minutes: selected.duration_minutes, rpe: selected.rpe, completion_rate: selected.completion_rate })}; {isZh ? "版本" : "revision"} {selected.revision}</p>
        <label>{isZh ? "更正时长（分钟）" : "Corrected duration"}<input type="number" min={1} max={1440} value={duration} onChange={(event) => setDuration(event.target.value)} /></label>
        <label>{isZh ? "更正整体强度" : "Corrected effort"}<input type="number" min={1} max={10} value={rpe} onChange={(event) => setRpe(event.target.value)} /></label>
        <label>{isZh ? "更正完成度（%）" : "Corrected completion (%)"}<input type="number" min={0} max={100} value={completion} onChange={(event) => setCompletion(event.target.value)} /></label>
        <label>{isZh ? "更正原因" : "Correction reason"}<textarea maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button onClick={confirm}>{isZh ? "确认更正这条记录" : "Confirm this correction"}</button>
        <button onClick={() => setSelected(null)}>{isZh ? "取消更正" : "Cancel"}</button>
      </fieldset>}
    </>}
  </section>;
}
