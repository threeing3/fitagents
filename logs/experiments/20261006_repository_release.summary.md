# Repository showcase release — 2026-10-06

## Motivation and scope
Publish the current Personal Agent implementation and readable documentation to
the default GitHub homepage. Preserve unrelated local output, secrets and databases.

## Initial failures and diagnosis
- PR #19 CI: legacy planner acceptance expected rule/LLM planning, but the new
  default lightweight dispatch intentionally reports `dispatch_reuse`.
  The legacy comparison now explicitly selects its legacy cascade policy;
  production routing remains unchanged.
- Restart fixture used field names while environment used Settings aliases.
  Explicit aliases isolate the fixture database and signing key from CI values.
- Public demo browser test still expected retired navigation and quota wording.
- Mobile trace layout needs bounded widths; retain the overflow assertion.
- Dependency audit flagged PyJWT 2.13.0. Pin 2.15.0, verified on official PyPI.

## Commands and results
Initial local Python run used Python 3.11.4 (the raw log filename erroneously
contains `py312`); it timed out during HTTP restart and is not a complete pass.
Focused and full checks will be recorded below before merging. No model calls,
patient data, paid services or quantitative efficacy experiment are involved.

Focused check 1: 4 restart/auth tests passed, 2 legacy planner tests failed.
Further diagnosis: intent provenance unconditionally labelled semantic cascade
as lightweight dispatch. Fix the label based on the actual policy so the host
does not incorrectly skip a requested legacy planner. Default policy unchanged.
Browser check 1: mobile trace passed; public demo still used a retired generation
button. The current dashboard deliberately navigates to conversation instead.

Focused check 2: both legacy planner tests passed; Ruff lint and format passed
for all 183 changed Python files; mypy passed. Public browser demo and mobile
trace tests passed. Python runtime dependency audit: no known vulnerabilities.
Frontend audit required source-map-js update (lockfile) and Vitest 4.1.11 patch
update; no major upgrade. Frontend build passed before those patch updates.
Windows full-suite rerun still hits the 30-second process-startup timeout in
crash tests under coverage. Keep CI's original gate unchanged; use an explicitly
labelled 90-second local diagnostic rerun and await both Linux CI matrices.
Current screenshots were visually inspected and use synthetic fixtures only.

First updated CI: frontend, lint and mypy passed; runtime and frontend dependency
audits passed. Secret scan flagged the literal synthetic test password in
`tests/test_subagent_catalog_api.py:17`. Added a path-and-line-scoped allowlist,
not a broad test-folder exclusion; full history scanning stays enabled.
After stopping only this turn's owned preview, npm installation recovered;
Vitest 4.1.11 component tests passed (92). Both dependency audits now report zero
known vulnerabilities. OpenAPI schema export passed (80 paths).

Secret allowlist diagnostic: the line-target regex did not match the scanner's
finding representation. Use an exact secret-value match (`synthetic1234`)
AND the single fixture path instead. No other passwords or files are exempted.
Updated frontend rerun: 92 component and 18 browser tests passed; build passed.
Linux CI frontend and Docker build passed before the scanner-only adjustment.
