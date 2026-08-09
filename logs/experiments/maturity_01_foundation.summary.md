# Maturity 01 Foundation — Sanitized Summary

## Motivation

Establish a reproducible CI and runtime foundation before product, algorithm, and SFT expansion.

## Scope

- Conditional feedback provenance contract.
- Blocking Python, frontend, container, schema, dependency, and secret gates.
- FastAPI lifespan and dependency-aware health checks.
- Same-origin multi-stage production image and CI-gated deployment workflow.
- Security dependency upgrades with PyJWT replacing python-jose/ecdsa.

## Results

- Local backend: 538 tests passed; whole-project coverage 65.50%.
- Local frontend: type check, component test, production build, and npm audit passed.
- npm audit: zero known vulnerabilities.
- OpenAPI: 52 paths exported.
- First remote CI run proved that clean clones lacked ignored local learning artifacts. The fix uses a two-row `synthetic_smoke` fixture only when the configured local SFT dataset is unavailable.

## Limitations and next step

- The foundation coverage gate is 65%; the public release target remains 70%.
- Local Docker was unavailable, so the remote Docker job is authoritative.
- Raw logs, user data, generated training datasets, databases, and model weights remain untracked.
