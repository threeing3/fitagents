import React, { useState } from "react";
import { Moon, Battery, Gauge, Heart, Utensils, Dumbbell, Send, CheckCircle } from "lucide-react";
import type { SessionState } from "./types";
import { submitCheckin } from "./api";

type Props = {
  session: SessionState | null;
  busy: boolean;
  setBusy: (v: boolean) => void;
  setNotice: (v: string) => void;
  onRefresh: () => void;
};

const MOODS = [
  { emoji: "😄", label: "Great" },
  { emoji: "🙂", label: "Good" },
  { emoji: "😐", label: "Okay" },
  { emoji: "😞", label: "Tired" },
  { emoji: "😤", label: "Stressed" },
];

const SLEEP_PRESETS = [4, 5, 6, 7, 8, 9, 10];

export function CheckinView({ session, busy, setBusy, setNotice, onRefresh }: Props) {
  const [sleep, setSleep] = useState(7);
  const [fatigue, setFatigue] = useState(3);
  const [soreness, setSoreness] = useState(2);
  const [stress, setStress] = useState(3);
  const [mood, setMood] = useState("");
  const [nutrition, setNutrition] = useState(80);
  const [workoutCompletion, setWorkoutCompletion] = useState(80);
  const [notes, setNotes] = useState("");
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit() {
    if (!session) return;
    setBusy(true);
    setSubmitted(false);
    try {
      const result = await submitCheckin(session.user_id, {
        sleep_hours: sleep,
        fatigue,
        soreness,
        stress,
        mood: mood || undefined,
        nutrition_adherence: nutrition,
        workout_completion: workoutCompletion,
        notes: notes || undefined,
      });
      setNotice(result.auto_adjusted ? "Check-in recorded & plan auto-adjusted." : "Check-in recorded.");
      setSubmitted(true);
      onRefresh();
      setTimeout(() => setSubmitted(false), 2500);
    } catch (err: any) {
      setNotice(`Check-in failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="checkin-view">
      <div className="checkin-header">
        <h2>Daily Check-in</h2>
        <p>How are you feeling today? This helps your coach adjust your training plan.</p>
      </div>

      <div className="checkin-grid">
        {/* Sleep */}
        <div className="checkin-card">
          <div className="checkin-card-head">
            <Moon size={20} />
            <span>Sleep</span>
          </div>
          <div className="sleep-presets">
            {SLEEP_PRESETS.map((h) => (
              <button key={h} className={`preset-btn ${sleep === h ? "active" : ""}`} onClick={() => setSleep(h)}>
                {h}h
              </button>
            ))}
          </div>
        </div>

        {/* Fatigue */}
        <SliderCard icon={<Battery size={20} />} label="Fatigue" value={fatigue} onChange={setFatigue} minLabel="Fresh" maxLabel="Exhausted" />

        {/* Soreness */}
        <SliderCard icon={<Gauge size={20} />} label="Soreness" value={soreness} onChange={setSoreness} minLabel="None" maxLabel="Very sore" />

        {/* Stress */}
        <SliderCard icon={<Heart size={20} />} label="Stress" value={stress} onChange={setStress} minLabel="Calm" maxLabel="High" />

        {/* Mood */}
        <div className="checkin-card">
          <div className="checkin-card-head">
            <Heart size={20} />
            <span>Mood</span>
          </div>
          <div className="mood-grid">
            {MOODS.map((m) => (
              <button key={m.label} className={`mood-btn ${mood === m.label ? "active" : ""}`} onClick={() => setMood(mood === m.label ? "" : m.label)}>
                <span className="mood-emoji">{m.emoji}</span>
                <span className="mood-label">{m.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Nutrition adherence */}
        <SliderCard icon={<Utensils size={20} />} label="Nutrition Adherence" value={nutrition} onChange={setNutrition} minLabel="Off track" maxLabel="Perfect" max={100} showPercent />

        {/* Workout completion */}
        <SliderCard icon={<Dumbbell size={20} />} label="Workout Completion" value={workoutCompletion} onChange={setWorkoutCompletion} minLabel="Skipped" maxLabel="100%" max={100} showPercent />

        {/* Notes */}
        <div className="checkin-card wide">
          <div className="checkin-card-head">
            <span>Notes</span>
          </div>
          <textarea
            className="checkin-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Anything else? Injuries, wins, struggles..."
            rows={3}
          />
        </div>
      </div>

      <div className="checkin-submit">
        <button className="submit-btn" onClick={handleSubmit} disabled={busy || !session}>
          {submitted ? <CheckCircle size={20} /> : <Send size={20} />}
          <span>{submitted ? "Recorded!" : busy ? "Saving..." : "Submit Check-in"}</span>
        </button>
      </div>
    </div>
  );
}

function SliderCard({
  icon,
  label,
  value,
  onChange,
  minLabel,
  maxLabel,
  max = 10,
  showPercent,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  onChange: (v: number) => void;
  minLabel: string;
  maxLabel: string;
  max?: number;
  showPercent?: boolean;
}) {
  return (
    <div className="checkin-card">
      <div className="checkin-card-head">
        {icon}
        <span>{label}</span>
        <strong className="slider-value">{showPercent ? `${value}%` : `${value}/${max}`}</strong>
      </div>
      <div className="slider-wrap">
        <span className="slider-min">{minLabel}</span>
        <input
          type="range"
          min={1}
          max={max}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="slider"
          style={{
            background: `linear-gradient(to right, #22c55e ${((value - 1) / (max - 1)) * 100}%, #2a2a3a ${((value - 1) / (max - 1)) * 100}%)`,
          }}
        />
        <span className="slider-max">{maxLabel}</span>
      </div>
    </div>
  );
}
