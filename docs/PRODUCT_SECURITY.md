# Public Demo Product Security

This document records the security and cost-control boundary of the public demo. It contains no credentials or user data.

## Authentication boundary

- Browser sessions use an `HttpOnly` cookie so JavaScript cannot read the credential. The cookie is `Secure` in production and uses `SameSite=Lax`.
- `Authorization: Bearer <token>` remains supported for scripts and API clients. When both are present, the explicit Bearer token wins.
- New registrations require a deployment-provided invite code whenever `INVITE_CODE` is configured. Production startup rejects a missing invite code.
- New passwords require at least 10 characters. Existing password hashes remain valid and existing accounts can still sign in.
- Login and registration have independent rate-limit buckets.

The legacy `ai_fitness_token` and `ai_fitness_user` browser storage keys are removed during frontend startup and are never repopulated.

## Demo account lifecycle

`POST /v1/auth/demo` signs into the server-managed demo account without returning or embedding its password in frontend code. The deployment secret is read only by the backend.

The daily reset is non-destructive:

1. The previous demo account receives an archived email and username.
2. All foreign-key-linked conversations, memories, logs and traces remain intact.
3. A fresh account and profile receive the public demo identity.
4. `demo_reset_states` records the active account, date and reset count.

No row is deleted by the reset path.

## Model-call quotas

`usage_events` stores a durable reservation immediately before a paid chat or vision client is created. The service checks both:

- `DAILY_MODEL_CALL_LIMIT` per authenticated user;
- `GLOBAL_DAILY_MODEL_LIMIT` across the deployment.

PostgreSQL reservations use a transaction-level advisory lock so concurrent requests cannot all pass a stale count. When either budget is exhausted, `ModelProvider` returns no live client and the existing deterministic rule fallback produces the response. `GET /v1/usage/summary` exposes counts and the fallback state without exposing prompts.

Reservations are intentionally conservative: a failed upstream call still consumes one application quota because a provider request may already have incurred cost. These counters are product safeguards, not provider billing statements.

## Production startup gate

`ENVIRONMENT=production` or `staging` rejects startup unless all required controls are safe:

- non-default JWT secret of at least 32 characters;
- invite code and protected metrics token;
- non-local PostgreSQL URL;
- live model provider plus matching API key;
- secure authentication cookie;
- no wildcard or localhost CORS origin;
- demo email/password when demo mode is enabled.

Same-origin deployments may leave `CORS_ORIGINS` empty. Secrets belong in the deployment platform, never in Git.

## Monitoring and uploads

- `/metrics` accepts the deployment token through `Authorization: Bearer` or `X-Metrics-Token` and otherwise returns HTTP 401.
- Food images are streamed with a byte ceiling, decoded with Pillow, checked against the declared JPEG/PNG/WebP type, and rejected on excessive pixel count or malformed content before a model call.
- Central error responses do not echo request bodies, passwords, cookies, API keys or raw image bytes.

## Remaining limitations

- The application quota day currently follows the server calendar day, not each user's timezone.
- Application reservations do not reconcile against provider billing exports.
- The current public algorithm summary is a fixed, source-labelled baseline; it is not an online business dashboard.
- Medical and injury boundaries remain deterministic code guardrails and the product makes no clinical claim.
