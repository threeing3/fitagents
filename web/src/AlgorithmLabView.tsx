import React, { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, FlaskConical, Route, ShieldCheck, Wrench } from "lucide-react";

import { compareIntent, fetchAgentChallenges, fetchAgentLabRun, fetchAgentLabRuns, fetchAlgorithmSummary } from "./api";
import { useLanguage } from "./LanguageContext";
import type { AgentChallengeSummary, AgentRunAnalysis, AlgorithmCompare, AlgorithmSummary } from "./types";

const pct = (value: number) => `${Math.round(value * 100)}%`;

function RoutingResult({ comparison, isZh }: { comparison: AlgorithmCompare; isZh: boolean }) {
  const routing = comparison.routing;
  const latency = comparison.latency_ms;
  return <div className="routing-result">
    <div className="routing-result-head"><b>{comparison.rule_baseline.primary_intent}</b><span>→</span><b>{comparison.runtime_decision.primary_intent}</b></div>
    <div className="routing-stages">
      <article><small>{isZh ? "规则基线" : "Rule baseline"}</small><strong>{routing.rules_evaluated ? "evaluated" : "skipped"}</strong><span>{latency.rule ?? 0} ms</span></article>
      <article><small>Qwen3-4B Adapter</small><strong>{routing.local_model_status}</strong><span>{latency.local_model ?? 0} ms</span></article>
      <article><small>DeepSeek fallback</small><strong>{routing.deepseek_used ? "used" : "not used"}</strong><span>{latency.model ?? 0} ms</span></article>
      <article><small>{isZh ? "最终来源" : "Final source"}</small><strong>{routing.final_source}</strong><span>{latency.total ?? 0} ms</span></article>
    </div>
    {routing.adapter_fallback_reason && <p className="routing-diagnostic">adapter fallback: <b>{routing.adapter_fallback_reason}</b>{routing.adapter_http_status ? ` · HTTP ${routing.adapter_http_status}` : ""}</p>}
    <p className={routing.safety_override_applied ? "routing-diagnostic safety" : "routing-diagnostic"}>{isZh ? "规则安全覆盖" : "Rule safety override"}: <b>{routing.safety_override_applied ? routing.safety_override_reasons.join(", ") : (isZh ? "未触发，规则仅参与评估" : "not triggered; rules evaluated only")}</b></p>
    {routing.local_model_version && <small>model: {routing.local_model_version} · tokens: {routing.local_model_usage.prompt_tokens ?? 0}/{routing.local_model_usage.completion_tokens ?? 0}</small>}
  </div>;
}

export function AlgorithmLabView() {
  const { isZh } = useLanguage();
  const [runs, setRuns] = useState<AgentRunAnalysis[]>([]);
  const [selected, setSelected] = useState<AgentRunAnalysis | null>(null);
  const [challenge, setChallenge] = useState<AgentChallengeSummary | null>(null);
  const [summary, setSummary] = useState<AlgorithmSummary | null>(null);
  const [compareMessage, setCompareMessage] = useState("我膝盖疼，但明天还能继续深蹲吗？");
  const [comparison, setComparison] = useState<AlgorithmCompare | null>(null);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([fetchAgentLabRuns(), fetchAgentChallenges()])
      .then(async ([runRows, challengeReport]) => {
        setRuns(runRows);
        setChallenge(challengeReport);
        if (runRows[0]) setSelected(await fetchAgentLabRun(runRows[0].run_id));
      })
      .catch((reason) => setError(String(reason)));
    fetchAlgorithmSummary().then(setSummary).catch(() => setSummary(null));
  }, []);

  const selectRun = async (runId: string) => {
    try { setSelected(await fetchAgentLabRun(runId)); } catch (reason) { setError(String(reason)); }
  };

  const runComparison = async () => {
    setComparing(true);
    setError("");
    try { setComparison(await compareIntent(compareMessage)); } catch (reason) { setError(String(reason)); }
    finally { setComparing(false); }
  };

  if (error) return <div className="algorithm-view empty-state"><AlertTriangle size={28} /><h2>{isZh ? "Agent Lab 暂不可用" : "Agent Lab unavailable"}</h2><p>{error}</p></div>;

  return <div className="algorithm-view agent-lab">
    <header className="algorithm-header"><div><span className="algorithm-kicker"><FlaskConical size={15} /> Agent Lab</span><h2>{isZh ? "决策回放与失败诊断" : "Decision replay and failure diagnosis"}</h2><p>{isZh ? "像复盘训练录像一样，检查 Agent 如何理解、规划、调用工具并完成安全校验。" : "Review how the Agent understands, plans, uses tools, verifies, and applies safety gates."}</p></div><span className="stage-badge">sanitized trace</span></header>
    <div className="algorithm-warning"><ShieldCheck size={18} /><span>{isZh ? "仅展示当前登录用户的脱敏投影，不返回原始输入、工具参数、模型上下文或日志路径。" : "Only a sanitized projection for the signed-in user is shown."}</span></div>

    {summary?.intent_inference && <section className="algorithm-panel challenge-panel"><div className="challenge-title"><div><span className="algorithm-kicker">intent routing</span><h3>{isZh ? "意图识别运行架构" : "Intent inference runtime"}</h3></div><strong>{summary.intent_inference.adapter_status}</strong></div><p className="muted">{isZh ? "确定性规则先做风险兜底；Qwen3-4B 适配器可用时负责语义分类，不可用时才回退 DeepSeek。页面不会把未验证的适配器标记为在线成果。" : "Rules retain safety authority; the Qwen3-4B adapter handles semantic classification when available, with DeepSeek as fallback."}</p><div className="decision-strip"><span>primary <b>Qwen3-4B QLoRA</b></span><span>fallback <b>DeepSeek</b></span><span>safety <b>rules</b></span></div><div className="failure-sample"><span>{isZh ? "单例实时对比（不计入离线指标）" : "Live single-case comparison (not an offline metric)"}</span><textarea value={compareMessage} onChange={(event) => setCompareMessage(event.target.value)} rows={3} /><button type="button" onClick={runComparison} disabled={comparing || !compareMessage.trim()}>{comparing ? (isZh ? "分析中…" : "Running…") : (isZh ? "运行意图对比" : "Compare intent")}</button>{comparison && <RoutingResult comparison={comparison} isZh={isZh} />}</div></section>}

    <section className="agent-lab-layout">
      <aside className="agent-run-list algorithm-panel"><h3><Route size={18} /> {isZh ? "最近执行" : "Recent runs"}</h3>{runs.length === 0 && <p className="muted">{isZh ? "完成一次聊天后，这里会出现可回放轨迹。" : "Complete a chat to create a replayable trace."}</p>}{runs.map((run) => <button type="button" className={selected?.run_id === run.run_id ? "agent-run active" : "agent-run"} key={run.run_id} onClick={() => selectRun(run.run_id)}><strong>{run.run_type}</strong><span>{run.status} · {run.node_count} nodes</span><small>{new Date(run.started_at).toLocaleString()}</small></button>)}</aside>
      <main className="algorithm-panel decision-replay"><h3>{isZh ? "决策轨道" : "Decision track"}</h3>{!selected && <p className="muted">{isZh ? "选择一次执行以查看决策链。" : "Select a run to inspect its decision chain."}</p>}{selected && <><div className="decision-strip"><span>intent <b>{selected.decision.intent}</b></span><span>planner <b>{selected.decision.planner_mode}</b></span><span>guardrail <b>{selected.decision.guardrail_action}</b></span><span>tools <b>{selected.tool_count}</b></span></div><ol className="trace-track">{selected.timeline.map((step) => <li key={`${step.order}-${step.node}`}><span className="trace-index">{step.order}</span><div><small>{step.phase_label} · {step.latency_ms} ms</small><strong>{step.node}</strong><p>{step.summary}</p></div></li>)}</ol></>}</main>
      <aside className="agent-findings algorithm-panel"><h3><Wrench size={18} /> {isZh ? "诊断发现" : "Findings"}</h3>{selected?.findings.map((finding) => <article className={`finding ${finding.severity}`} key={`${finding.code}-${finding.node}`}><strong>{finding.severity === "info" ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}{finding.title}</strong><p>{finding.detail}</p><small>{finding.node} · {finding.code}</small></article>)}{!selected && <p className="muted">{isZh ? "暂无运行诊断。" : "No run diagnosis yet."}</p>}</aside>
    </section>

    {challenge && <section className="algorithm-panel challenge-panel"><div className="challenge-title"><div><span className="algorithm-kicker">challenge_eval · test only</span><h3>{isZh ? "高难度挑战基线" : "High-difficulty challenge baseline"}</h3></div><strong>{challenge.passed}/{challenge.cases}</strong></div><p className="muted">{isZh ? "诊断集不进入训练。组合门禁要求每个判断同时正确，因此保留当前低分作为调优起点。" : "This diagnostic set is excluded from training. The exact gate requires every decision to be correct."}</p><div className="component-scores">{Object.entries(challenge.component_scores).map(([name, value]) => <div key={name}><span>{name}</span><i><b style={{ width: pct(value) }} /></i><strong>{pct(value)}</strong></div>)}</div><div className="failure-sample"><span>{isZh ? "失败样例" : "Failure example"}</span><p>{challenge.failure_examples[0]?.user_message || "—"}</p><small>{challenge.failure_examples[0]?.category} · {challenge.failure_count} failures</small></div></section>}
  </div>;
}
