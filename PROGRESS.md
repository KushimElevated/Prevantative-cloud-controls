# Progress

Status as of 2026-10-08: all four planned increments are implemented and were executed in this environment,
plus the optional A2UI-powered **AI Control Workspace** (increment 5, below).

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

5. **Optional AI Control Workspace** (`docs/a2ui-workspace.md`), behind `ENABLE_A2UI_WORKSPACE`:
   - Phase 1, foundation: feature flag, experience selector with per-user preference (migration 0003,
     `users.preferences`), `/workspace` route, approved A2UI v0.9 catalog (12 domain + 2 layout components) with
     strict server and client contracts, server and client payload validation, error boundary.
   - Phase 2, investigation: typed read tools over the existing services (caller-scoped), deterministic intents,
     the Azure AI Search production impact investigation, context and actions panel, deep links both ways.
   - Phase 3, AI orchestration: provider abstraction, Anthropic provider (official SDK, read-only strict tools,
     JSON plan, refusal fallbacks), strict plan validation, deterministic fallback, audit of every AI run.
   - Phase 4, guided workflows: exception, rollout-plan and control drafts with stale-basis and blocker checks,
     submitted through the existing services after confirmation; approvals and handoff stay in Classic.

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
| Workspace backend tests (local PostgreSQL 16) | `cd backend && .venv/bin/pytest -q` | **231 passed** (49 existing + 182 workspace: validator, deterministic, AI with scripted provider and stubbed SDK client, hardening, review regressions) |
| Workspace frontend | `npx tsc --noEmit`; `npx vitest run`; `npx next build` | passed; **85** unit tests (10 existing + 75 workspace); `/workspace` builds with the renderer in a separate client-only chunk |
| Adversarial review | 4 lens reviewers + 4 verifiers over the feature diff | 22 confirmed findings (0 critical/high), 4 refuted; all fixed with regression tests, then every suite above re-run |
| Browser journeys (local API + `next start`, after `reset`) | `npx playwright test` | **4 passed**: Classic journey, workspace selector/investigation/scope/deep links, confirmed assessment from the workspace, scoped requester exception draft |
| Flag off | API with `ENABLE_A2UI_WORKSPACE=false` | Classic journey passed; no nav entry, selector or deep link; `/workspace` shows the disabled notice; `/api/v1/workspace/*` returns 404 |

## Issues found and fixed during the build

Workspace increment:
- The pinned A2UI renderer validates props but silently accepts unknown component types: the client now
  pre-validates every payload against the catalog before rendering.
- AI audit events were catalogue-level and readable by every scoped user; they are now scoped.
- A scope named in a question produced a different message for unreadable vs unknown scopes (existence oracle).
- "Who approved ..." was classified as a rollout question; intent order fixed.
- Draft submission trusted a client-supplied duplicate search and did not re-check rollout preconditions; both
  are now recomputed on the server (409 `DRAFT_BLOCKED`).
- Safe-link pattern accepted `.`/`..` path segments; rejected on both sides.
- Transitive `dompurify` 3.4.11 (via `@a2ui/markdown-it`) had advisories; overridden to 3.4.16.
- From the adversarial review: an oversized or contract-violating view returned HTTP 500 (now degraded to a
  notice); a user with no readable scope of the control's provider saw another scope's run as evidence;
  account-level readiness blockers were attributed to one application; the coverage matrix dropped
  "not enforceable by mechanism" resources; a selected scope lost to one named in the question; the readiness
  example question matched no control and generic words matched the wrong one; "pre-production" meant
  production; rollout drafts could name a plan the caller cannot read; AI audit events were scoped to the
  pre-AI scope; the model could override the user's chosen scope or control and its ids were echoed into
  notes; link stripping was bypassable; slow AI calls could hold every pooled connection; client contracts
  counted UTF-16 units instead of code points; untagged-resource exception drafts and control drafts could
  not be completed; "Prepare again" discarded typed values; a late response after leaving the workspace
  navigated back to it.

Core MVP:

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
production SSO, real telemetry, automated evidence reconciliation. AI-assisted workspace mode is implemented
but was not run against the live Anthropic API here (no credentials); it is covered by a scripted provider and
a stubbed SDK client. Pre-existing: `npm audit` reports advisories for Next.js 14.2.x whose fix requires a major
upgrade (Next 16), not attempted in this increment. AWS documentation must be
verified from primary sources before the SCP implementation can be integration-ready.

## Exact next commands

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose run --rm ops python -m app.cli reset
docker compose run --rm ops pytest
cd frontend && npm ci && npx vitest run && npx playwright test   # needs ENABLE_A2UI_WORKSPACE=true for workspace.spec.ts
```
