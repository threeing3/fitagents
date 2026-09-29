import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle, Dumbbell, Plus, RotateCcw, Send, Trash2 } from "lucide-react";

import { logWorkout } from "./api";
import { useLanguage } from "./LanguageContext";
import type { SessionState } from "./types";

type Props = {
  session: SessionState | null;
  busy: boolean;
  setBusy: (value: boolean) => void;
  setNotice: (value: string) => void;
  onRefresh: () => void;
};

type ExerciseRow = {
  id: string;
  name: string;
  sets: number;
  reps: number;
  weight: number;
};

type WorkoutPayload = {
  workout_name: string;
  duration_minutes: number;
  rpe: number;
  completion_rate: number;
  notes?: string;
  exercises: Array<{
    name: string;
    sets: Array<{ reps: number; weight: number; rpe: number; completed: boolean }>;
  }>;
};

type PendingWorkout = {
  key: string;
  payload: WorkoutPayload;
  createdAt: string;
};

function exerciseRow(): ExerciseRow {
  return { id: crypto.randomUUID(), name: "", sets: 3, reps: 10, weight: 0 };
}

function pendingStorageKey(userId: string): string {
  return `fitagent_pending_workout_${userId}`;
}

function readPending(userId: string): PendingWorkout | null {
  try {
    const raw = sessionStorage.getItem(pendingStorageKey(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingWorkout;
    if (!parsed.key || !parsed.payload?.workout_name || !Array.isArray(parsed.payload.exercises)) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function WorkoutView({ session, busy, setBusy, setNotice, onRefresh }: Props) {
  const { isZh } = useLanguage();
  const [workoutName, setWorkoutName] = useState(isZh ? "力量训练" : "Strength workout");
  const [duration, setDuration] = useState(45);
  const [rpe, setRpe] = useState(6);
  const [completion, setCompletion] = useState(100);
  const [notes, setNotes] = useState("");
  const [exercises, setExercises] = useState<ExerciseRow[]>([exerciseRow()]);
  const [pending, setPending] = useState<PendingWorkout | null>(null);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!session) {
      setPending(null);
      return;
    }
    setPending(readPending(session.user_id));
  }, [session]);

  const formLocked = busy || pending !== null;
  const validExercises = useMemo(
    () => exercises.filter((exercise) => exercise.name.trim()),
    [exercises],
  );

  function updateExercise(id: string, patch: Partial<ExerciseRow>) {
    setExercises((rows) => rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }

  function buildPayload(): WorkoutPayload | null {
    const name = workoutName.trim();
    if (!name || duration < 1 || validExercises.length === 0) {
      setNotice(
        isZh
          ? "请填写训练名称、有效时长和至少一个动作。"
          : "Add a workout name, valid duration, and at least one exercise.",
      );
      return null;
    }
    return {
      workout_name: name,
      duration_minutes: duration,
      rpe,
      completion_rate: completion / 100,
      notes: notes.trim() || undefined,
      exercises: validExercises.map((exercise) => ({
        name: exercise.name.trim(),
        sets: Array.from({ length: exercise.sets }, () => ({
          reps: exercise.reps,
          weight: exercise.weight,
          rpe,
          completed: true,
        })),
      })),
    };
  }

  async function submitPending(request: PendingWorkout) {
    if (!session) return;
    setBusy(true);
    setSubmitted(false);
    try {
      const result = await logWorkout(session.user_id, request.payload, request.key);
      sessionStorage.removeItem(pendingStorageKey(session.user_id));
      setPending(null);
      setSubmitted(true);
      setNotice(
        result.idempotent_replay
          ? isZh
            ? "已确认上次训练其实保存成功，本次没有重复记录。"
            : "The earlier workout was confirmed; no duplicate was created."
          : isZh
            ? "训练记录已保存。"
            : "Workout saved.",
      );
      onRefresh();
      setTimeout(() => setSubmitted(false), 2500);
    } catch (error: any) {
      setNotice(
        isZh
          ? `结果尚未确认：${error.message}。请保留本页并重试原提交。`
          : `Outcome not confirmed: ${error.message}. Keep this page and retry the original submission.`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmit() {
    if (!session || pending) return;
    const payload = buildPayload();
    if (!payload) return;
    const request = { key: crypto.randomUUID(), payload, createdAt: new Date().toISOString() };
    sessionStorage.setItem(pendingStorageKey(session.user_id), JSON.stringify(request));
    setPending(request);
    await submitPending(request);
  }

  function startAnotherWorkout() {
    setSubmitted(false);
    setWorkoutName(isZh ? "力量训练" : "Strength workout");
    setDuration(45);
    setRpe(6);
    setCompletion(100);
    setNotes("");
    setExercises([exerciseRow()]);
  }

  return (
    <div className="workout-view">
      <div className="checkin-header">
        <h2>{isZh ? "训练记录" : "Workout log"}</h2>
        <p>
          {isZh
            ? "记录本次训练；网络中断后会使用同一请求键恢复，不会重复写入。"
            : "Record a session. Network recovery reuses the same request key to avoid duplicates."}
        </p>
      </div>

      {pending && (
        <div className="workout-recovery" role="alert">
          <AlertTriangle size={20} />
          <div>
            <strong>{isZh ? "有一条结果未确认的训练" : "A workout outcome is unconfirmed"}</strong>
            <span>
              {isZh
                ? "表单已冻结。请重试原提交，系统会查询原结果而不是新建记录。"
                : "The form is locked. Retry the original request so the server can recover its result."}
            </span>
          </div>
          <button type="button" onClick={() => submitPending(pending)} disabled={busy}>
            <RotateCcw size={16} />
            {busy ? (isZh ? "恢复中…" : "Recovering...") : isZh ? "重试原提交" : "Retry original"}
          </button>
        </div>
      )}

      <div className="workout-grid">
        <label className="workout-field wide">
          <span>{isZh ? "训练名称" : "Workout name"}</span>
          <input
            aria-label={isZh ? "训练名称" : "Workout name"}
            value={workoutName}
            onChange={(event) => setWorkoutName(event.target.value)}
            disabled={formLocked}
          />
        </label>
        <label className="workout-field">
          <span>{isZh ? "时长（分钟）" : "Duration (minutes)"}</span>
          <input
            aria-label={isZh ? "时长（分钟）" : "Duration (minutes)"}
            type="number"
            min={1}
            max={600}
            value={duration}
            onChange={(event) => setDuration(Number(event.target.value))}
            disabled={formLocked}
          />
        </label>
        <label className="workout-field">
          <span>{isZh ? "训练完成度" : "Completion"}</span>
          <input
            aria-label={isZh ? "训练完成度" : "Completion"}
            type="range"
            min={0}
            max={100}
            value={completion}
            onChange={(event) => setCompletion(Number(event.target.value))}
            disabled={formLocked}
          />
          <strong>{completion}%</strong>
        </label>
        <label className="workout-field wide">
          <span>{isZh ? "整体主观用力程度（RPE）" : "Overall effort (RPE)"}</span>
          <input
            aria-label={isZh ? "整体主观用力程度（RPE）" : "Overall effort (RPE)"}
            type="range"
            min={1}
            max={10}
            value={rpe}
            onChange={(event) => setRpe(Number(event.target.value))}
            disabled={formLocked}
          />
          <strong>{rpe}/10</strong>
        </label>
      </div>

      <section className="exercise-editor">
        <div className="exercise-editor-head">
          <div>
            <h3><Dumbbell size={18} /> {isZh ? "动作明细" : "Exercises"}</h3>
            <p>{isZh ? "每个动作按相同组数、次数和重量记录。" : "Each exercise uses the entered sets, reps, and weight."}</p>
          </div>
          <button type="button" onClick={() => setExercises((rows) => [...rows, exerciseRow()])} disabled={formLocked}>
            <Plus size={16} /> {isZh ? "添加动作" : "Add exercise"}
          </button>
        </div>
        <div className="exercise-list">
          {exercises.map((exercise, index) => (
            <div className="exercise-row" key={exercise.id}>
              <label>
                <span>{isZh ? `动作 ${index + 1}` : `Exercise ${index + 1}`}</span>
                <input
                  aria-label={isZh ? `动作 ${index + 1}` : `Exercise ${index + 1}`}
                  placeholder={isZh ? "例如：哑铃划船" : "e.g. dumbbell row"}
                  value={exercise.name}
                  onChange={(event) => updateExercise(exercise.id, { name: event.target.value })}
                  disabled={formLocked}
                />
              </label>
              <label>
                <span>{isZh ? "组数" : "Sets"}</span>
                <input type="number" min={1} max={20} value={exercise.sets} onChange={(event) => updateExercise(exercise.id, { sets: Number(event.target.value) })} disabled={formLocked} />
              </label>
              <label>
                <span>{isZh ? "次数" : "Reps"}</span>
                <input type="number" min={1} max={200} value={exercise.reps} onChange={(event) => updateExercise(exercise.id, { reps: Number(event.target.value) })} disabled={formLocked} />
              </label>
              <label>
                <span>{isZh ? "重量（kg）" : "Weight (kg)"}</span>
                <input type="number" min={0} max={1000} step={0.5} value={exercise.weight} onChange={(event) => updateExercise(exercise.id, { weight: Number(event.target.value) })} disabled={formLocked} />
              </label>
              <button className="exercise-remove" aria-label={isZh ? `删除动作 ${index + 1}` : `Remove exercise ${index + 1}`} type="button" onClick={() => setExercises((rows) => rows.filter((row) => row.id !== exercise.id))} disabled={formLocked || exercises.length === 1}>
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
      </section>

      <label className="workout-field workout-notes">
        <span>{isZh ? "训练备注" : "Notes"}</span>
        <textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder={isZh ? "例如疼痛、动作调整或本次进步……" : "Pain, exercise changes, or progress..."}
          rows={3}
          disabled={formLocked}
        />
      </label>

      <div className="workout-actions">
        <button className="submit-btn" type="button" onClick={handleSubmit} disabled={formLocked || !session}>
          {submitted ? <CheckCircle size={20} /> : <Send size={20} />}
          {submitted ? (isZh ? "已保存" : "Saved") : busy ? (isZh ? "保存中…" : "Saving...") : isZh ? "保存训练" : "Save workout"}
        </button>
        {submitted && (
          <button className="secondary-btn" type="button" onClick={startAnotherWorkout}>
            <Plus size={16} /> {isZh ? "记录下一场训练" : "Log another workout"}
          </button>
        )}
      </div>
    </div>
  );
}
