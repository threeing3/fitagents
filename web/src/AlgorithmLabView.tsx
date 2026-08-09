import React, { useEffect, useState } from "react";
import { AlertTriangle, BarChart3, Database, FlaskConical, ShieldCheck } from "lucide-react";

import { fetchAlgorithmSummary } from "./api";
import { useLanguage } from "./LanguageContext";
import type { AlgorithmSummary } from "./types";

export function AlgorithmLabView() {
  const { isZh } = useLanguage();
  const [summary, setSummary] = useState<AlgorithmSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchAlgorithmSummary().then(setSummary).catch((reason) => setError(String(reason)));
  }, []);

  if (error) {
    return (
      <div className="algorithm-view empty-state">
        <AlertTriangle size={28} />
        <h2>{isZh ? "算法报告暂不可用" : "Algorithm report unavailable"}</h2>
        <button type="button" onClick={() => window.location.reload()}>
          {isZh ? "重新加载" : "Reload"}
        </button>
      </div>
    );
  }

  if (!summary) {
    return <div className="algorithm-view empty-state">{isZh ? "正在读取脱敏实验摘要…" : "Loading sanitized experiment summary…"}</div>;
  }

  return (
    <div className="algorithm-view">
      <header className="algorithm-header">
        <div>
          <span className="algorithm-kicker"><FlaskConical size={15} /> Algorithm Lab</span>
          <h2>{isZh ? "算法可信度与实验进度" : "Algorithm evidence and experiment progress"}</h2>
          <p>{isZh ? "页面只展示脱敏、固定且标注来源的证据。" : "Only sanitized, fixed, source-labelled evidence is shown."}</p>
        </div>
        <span className="stage-badge">{summary.release_stage}</span>
      </header>

      <div className="algorithm-warning">
        <ShieldCheck size={18} />
        <span>{isZh ? "当前不声明真实线上业务提升；业务结果统一标记为 simulated_outcome（模拟结果）。" : summary.disclaimer}</span>
      </div>

      <section className="algorithm-grid">
        {summary.metrics.map((metric) => (
          <article className="algorithm-card" key={metric.name}>
            <BarChart3 size={18} />
            <strong>{metric.value}{metric.total ? ` / ${metric.total}` : ""}{metric.unit === "percent" ? "%" : ""}</strong>
            <span>{metric.name}</span>
            <small>{isZh ? "来源" : "source"}: {metric.source}</small>
          </article>
        ))}
      </section>

      <section className="algorithm-panel">
        <h3><Database size={18} /> {isZh ? "数据集来源与状态" : "Dataset provenance and status"}</h3>
        <div className="dataset-table">
          <div className="dataset-row dataset-head">
            <span>{isZh ? "数据集" : "Dataset"}</span><span>{isZh ? "规模" : "Size"}</span><span>{isZh ? "来源" : "Source"}</span><span>{isZh ? "状态" : "Status"}</span>
          </div>
          {summary.datasets.map((dataset) => (
            <div className="dataset-row" key={dataset.name}>
              <code>{dataset.name}</code><span>{dataset.size}</span><span>{dataset.source}</span><span>{dataset.status}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="algorithm-panel gate-panel">
        <h3>{isZh ? "后训练发布门禁" : "Post-training release gate"}</h3>
        <p>
          DPO（直接偏好优化）: {summary.dpo.enabled ? "enabled" : "disabled"} · {summary.dpo.current_reviewed_pairs} / {summary.dpo.minimum_reviewed_pairs} {isZh ? "对真实审核偏好数据" : "human-reviewed preference pairs"}
        </p>
      </section>
    </div>
  );
}
