# Progress

Status as of 2026-10-08: all four planned increments are implemented and were executed in this environment.

## Completed

1. **Foundation**: Docker Compose (db, migrate, backend, frontend, ops) with health checks; Alembic migrations
   (schema + immutability triggers + app-role grants); deterministic seed/reset CLI; local demo authentication
   and scope-aware authorization; control and implementation revisions with immutable submission.
2. **Azure assessment and readiness**: verified built-in definition pinned by digest; strict versioned
   evaluator; persisted assessment runs/results with reconciling counts, request impact, readiness, baseline,
   blockers and confidence categories; readiness evidence; Control Detail page.
3. **Governance and delivery**: exceptions (governance vs native status, representability, renewal lineage,
   clock-derived validity); rollout plans and rings; change packages bound to manifest digests; gates enforced
   in the backend; separation of duties; deterministic bundle export (approved and draft); mock pipeline and
   receipt validation; fixture observations; drift; idempotent reconciliation; append-only audit.
4. **AWS and remaining views**: AWS SCP provider (no audit mode, prerequisite baseline, inherited deny,
   unrepresentable exceptions, demo-only gating); dashboard with defined denominators; assessments,
   exceptions, rollouts, bundles, audit, implementation and simulation-disclosure views; tests; documentation.

## Verified by execution (this environment)

| Check | Command | Result |
|---|---|---|
| Fresh setup | `docker compose down -v && docker compose up -d --build --wait` | db, backend, frontend healthy; `migrate` exited 0 after applying 0001 and 0002 |
| Seeding | `docker compose run --rm ops python -m app.cli seed` | seeded 10 users, 3 controls; second `seed` refused with exit 1 |
| Repeatable reset | `docker compose run --rm ops python -m app.cli reset` | succeeded repeatedly |
| Backend tests | `docker compose run --rm ops pytest` | **49 passed** (also 49 passed outside Docker against a local Postgres 16) |
| Frontend type check / build | `npx tsc --noEmit`; `next build` (also inside the frontend image build) | passed |
| Frontend component tests | `npx vitest run` | **10 passed** |
| Browser smoke test | `reset`, then `npx playwright test` against the Compose stack | **1 passed** (full Azure journey to VERIFIED) |
| Persistence after restart | `docker compose down` (volumes kept) then `up --wait` | delivery targets still VERIFIED, coverage 1/7, audit history intact |
| Live deployment refused | `docker compose run --rm -e ENABLE_LIVE_DEPLOYMENT=true backend` | startup failed with `ConfigurationError` |
| Non-local demo auth refused | `-e APP_ENV=production` / `-e AUTH_MODE=oidc` with `check-config` | exit 2 with explicit messages |
| Reconciliation idempotency | `make reconcile` twice | first run: 2 expired, 1 removal handoff, 1 cleanup item; second run: no changes |

## Issues found and fixed during the build

- Assessment results could be flushed before their (append-only) run row: runs are now inserted first.
- "Latest" ordering depended on timestamps, which collide under a fixed test clock: added monotonic `seq`
  columns for assessment runs, validations, receipts and observations.
- Readiness for resources on an approved exception path wrongly required private-endpoint evidence.
- Manifests changed when this platform's own deliveries were observed (baseline/exception additions); the
  baseline now excludes bindings delivered by the same plan, and exception additions do not depend on native state.
- Docker builds failed behind the build environment's TLS-intercepting proxy: added an optional `extra_ca`
  build secret (empty by default).

## Not done / deferred (by design)

Live inventory readers, existing-policy import, GitHub PR integration, authenticated pipeline callbacks,
production SSO, real telemetry, automated evidence reconciliation, AI assistance. AWS documentation must be
verified from primary sources before the SCP implementation can be integration-ready.

## Exact next commands

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose run --rm ops python -m app.cli reset
docker compose run --rm ops pytest
cd frontend && npm ci && npx vitest run && npx playwright test
```
