# CI and Deployment Gates

This document describes the executable foundation gates for the public demo. It never contains deployment secrets or user data.

## Pull request gates

Every pull request to `main` must pass:

1. Ruff lint and format checks for every changed Python file.
2. Mypy checks for the typed configuration, database, and training-data contract boundary.
3. The complete backend test suite on Python 3.11 and 3.12.
4. Whole-project coverage of at least 65% during the foundation phase. The public-release target remains 70% and must be enabled before the release PR.
5. Frontend TypeScript checking, Vitest component tests, and the Vite production build.
6. Full-stack multi-stage Docker image build.
7. OpenAPI schema generation.
8. Python and npm dependency audits plus Gitleaks secret scanning.

Quality checks are blocking. There is no `continue-on-error` path for lint, types, tests, builds, or security scans.

## Local verification

Run commands from the repository root unless a command explicitly changes directory:

```powershell
.\.venv\Scripts\python.exe -m compileall -q fast_api algorithm tests
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest tests -q --timeout=30 --cov=fast_api/app --cov=algorithm --cov-fail-under=65

Push-Location web
npm ci
npm run typecheck
npm test
npm run build
npm audit --audit-level=high --registry=https://registry.npmjs.org
Pop-Location

docker build -t ai-fitness-coach:local .
```

## Deployment gate

The deployment workflow runs only after the `CI` workflow succeeds on `main`, or after a manual dispatch. Deployment remains disabled until the repository variable below is explicitly enabled.

Repository variables:

- `DEPLOY_ENABLED=true` enables the deployment job.
- `DYNAMIC_DEMO_URL` is the public origin without a trailing slash.

Repository secret:

- `RENDER_DEPLOY_HOOK_URL` is the private Render deployment-hook URL.

When enabled, the workflow publishes the full-stack image to GitHub Container Registry, triggers Render, polls `/health/ready`, and smoke-tests `/health/live`, `/health`, and `/`.

## Health semantics

- `/health/live` confirms that the process can answer HTTP traffic and does not query dependencies.
- `/health/ready` requires a database connection, a non-empty Alembic version, and valid model and embedding configuration. It returns HTTP 503 until all checks pass.
- `/health` remains available for compatibility and reports the selected model providers without exposing secret values.
