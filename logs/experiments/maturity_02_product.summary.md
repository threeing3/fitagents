# Maturity 02 Product — Reviewed Experiment Summary

- Experiment ID: `maturity_02_product_20260809`
- Branch: `codex/maturity-02-product`
- Evidence date: `2026-08-09`
- Scope: public-demo authentication, safety, cost control, bilingual UX, and algorithm evidence UI

## Motivation

The foundation PR made CI and the production image reproducible. This experiment adds the minimum product boundary required before a public dynamic demo can accept accounts, messages, feedback, and paid model calls.

## Implemented controls

1. Browser authentication moved from script-readable local storage to an HttpOnly, SameSite cookie; Bearer authentication remains compatible.
2. Registration gained an invite code, independent auth rate limits, and a 10-character new-password minimum while preserving legacy login hashes.
3. Additive migration `013_product_safety_and_usage` introduced durable `usage_events` and `demo_reset_states`; rollback deliberately deletes nothing.
4. Per-user and global daily model quotas reserve a database event before creating a paid client. Exhaustion selects the deterministic offline path.
5. The daily demo reset archives the previous identity and retains every linked record.
6. Production startup rejects default secrets, local databases, unsafe cookies/CORS, missing invite/metrics controls, and missing model credentials.
7. Metrics require a token; uploaded food images are byte-, type-, pixel-, and decode-validated before inference.
8. The frontend defaults to Chinese, can switch to English, and includes cold-start, privacy, medical-boundary, demo-account, quota, and recovery messaging.
9. Algorithm Lab exposes only fixed, sanitized, source-labelled baseline evidence. Business outcomes remain labelled `simulated_outcome`; DPO remains disabled.

## Reproducible verification

| Gate | Result |
|---|---:|
| Backend tests | 551 / 551 passed |
| Whole-project coverage | 66.07% |
| Python compile / Ruff / Mypy | passed |
| Frontend type check | passed |
| Frontend component tests | 1 / 1 passed |
| Playwright Chromium E2E | 1 / 1 passed |
| Frontend production build | passed |
| Frontend high-severity audit | 0 vulnerabilities |
| OpenAPI generation | 56 paths validated |
| Docker build | passed, ~124 MB |
| Container import smoke | passed |

The final frontend gate was rerun from a clean `npm ci`: type check, component test, production build, and Chromium E2E all passed. The component test includes the real language provider and verifies that legacy script-readable credentials are cleared before switching the UI to English.

The first remote run found one test-isolation failure: CI's `LLM_PROVIDER=offline` environment value overrode a field-name constructor argument in the quota fixture. The fixture now uses the settings' public aliases explicitly; production quota behavior was unchanged.

The local Python dependency audit timed out while querying the network and therefore has no local pass result. The GitHub CI security job remains the blocking source of truth for Python dependency and secret scans.

## Evidence provenance

- Test, coverage, build, and image values above are real local execution evidence.
- Algorithm dataset sizes and the 27/38 business baseline are fixed repository evidence carried forward from the maturity baseline.
- No metric in this report is a real online acceptance, adherence, or revenue uplift.
- No dataset is newly labelled `expert_labeled` by this phase.

## Known limitations and next gates

- The release target remains 70% whole-project coverage; this phase reached 66.07% and does not claim final compliance.
- The fixed business suite remains 27/38 until the algorithm PR.
- Model-call reservations are conservative application counters, not provider billing reconciliation.
- Quota days follow the server calendar rather than each user's timezone.
- Dynamic deployment secrets, Render/Neon connectivity, and 72-hour public observation belong to the release PR.
