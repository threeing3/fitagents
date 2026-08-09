# Maturity 03 Algorithms — Reviewed Experiment Summary

- Experiment ID: `maturity_03_algorithms_20260809`
- Branch: `codex/maturity-03-algorithms`
- Evidence date: `2026-08-09`
- Status: locally validated

## Scope

This phase establishes training-isolated fixed evaluations, removes pseudo-vector claims, hardens deterministic planning and safety gates, and produces a source-labelled synthetic training-factory baseline.

## Evidence boundaries

- Fixed evaluation rows use `source=seed_eval`, `partition=test`, and are never training eligible.
- Factory rows use `source=synthetic`; their outcomes use `label_source=simulated_outcome`.
- No row is relabelled as expert reviewed.
- Vector metrics are unavailable unless scores carry embedding-service provenance.
- DPO remains blocked until at least 150 genuinely human-reviewed preference pairs exist.

## Results

| Gate | Result |
|---|---:|
| Fixed business cases | 38 / 38 passed |
| Intent evaluation | 120 cases; Macro-F1 1.00; risk recall 1.00 |
| Retrieval evaluation | 80 queries; BM25 Recall@5 0.95; vector unavailable |
| Tool planning evaluation | 200 cases; rule selection/sequence/schema 1.00; unnecessary 0.00 |
| Safety evaluation | 150 cases; risk recall 1.00; critical dangerous allowed 0 |
| Response quality evaluation | 100 cases; safety hard gate 1.00 |
| Training factory | 1200 synthetic; 960/120/120; 0 user leaks; 0 reviewed preferences |
| Backend regression | 565 passed; coverage 65.90% |
| Local Docker build | inconclusive: local runner timed out at 10 minutes; CI required |

All evaluation fixtures are rule-curated fixed coverage sets. The perfect intent and safety scores are release-regression evidence, not a claim about production-distribution generalization. The 70% final coverage target remains open.

Public evidence:

- `algorithm/evaluation/reports/maturity_03_baseline.summary.json`
- `algorithm/datasets/manifests/maturity_03_synthetic.summary.json`
