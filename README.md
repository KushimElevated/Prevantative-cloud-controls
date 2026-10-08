# Cloud Security Control Engineering Platform (local MVP)

Decide which classes of cloud security problems should become impossible within a defined scope, and turn
those requirements into **governed, verifiable preventive controls**. Security intent is the primary object;
native policies, assignments, prerequisites and processes implement it. Every prevention claim carries its
scope, dependencies, evidence and limitations.

The first useful outcome is a **reviewable control-change package** that Cloud Engineering can accept, with
evidence of the risk addressed, likely impact, prerequisites, exceptions, approvals and a rollout plan.

> **Local demo only.** All inventory, request, pipeline and observation data are fixtures or mock data.
> No cloud credentials, external accounts, AI services or runtime network calls are used. Nothing is deployed.

This is **not** a CNAPP/CSPM, a Wiz replacement, a provisioning portal, an IAM/JIT platform, a CMDB, a general
remediation tracker, or a replacement for AWS Organizations, Azure Policy, Terraform or GitHub Actions.

## Prerequisites

- Docker Engine with Docker Compose v2 (tested with Docker 29.8 / Compose 5.6)
- For frontend tests outside Docker: Node.js 22 and npm
- Optional for backend development outside Docker: Python 3.12+ and [uv](https://docs.astral.sh/uv/)

## Run it

```bash
cp .env.example .env            # local-only demo credentials; edit if you like
docker compose up -d --build --wait
docker compose run --rm ops python -m app.cli seed     # first time only
```

- UI: http://localhost:3000 (pick a demo identity)
- API docs: http://localhost:8000/api/docs, OpenAPI: http://localhost:8000/api/openapi.json (copy in `docs/openapi.json`)
- Health: http://localhost:8000/api/health

`docker compose up` starts PostgreSQL, applies Alembic migrations with the owner role (`migrate`), then
starts the API (application role) and the frontend, each with a health check. Data persists in the `pgdata`
volume across `docker compose down` / `up` (use `down -v` to delete it).

Behind a TLS-intercepting proxy, set `EXTRA_CA_CERT=/path/to/ca.pem` in `.env` so image builds trust it.

## Seed and reset

```bash
docker compose run --rm ops python -m app.cli seed       # refuses if data already exists
docker compose run --rm ops python -m app.cli reset      # downgrade -> upgrade -> deterministic seed (repeatable)
docker compose run --rm ops python -m app.cli reset --anchor 2026-10-08T12:00:00Z   # fully reproducible timestamps
docker compose run --rm ops python -m app.cli reconcile  # idempotent expiry/drift reconciliation
```

Seed timestamps are relative to the current hour so freshness windows behave sensibly; run `reset` if you come
back to the demo days later. `make` targets wrap the same commands (`make up`, `make reset`, `make test`, ...).

## Tests

```bash
docker compose run --rm ops pytest                     # backend: 49 domain + API integration tests on real PostgreSQL
cd frontend && npm ci && npx tsc --noEmit && npx vitest run   # frontend type check + component tests
docker compose run --rm ops python -m app.cli reset && (cd frontend && npx playwright test)   # browser smoke test (stack running)
```

Backend tests migrate a separate `ccp_test` database from scratch (owner role), seed it with a fixed anchor,
and run each test inside a rolled-back transaction using the application role, so triggers and grants are
exercised. The Playwright test drives the complete Azure journey through the UI (set `E2E_BASE_URL` if not
`http://localhost:3000`).

## Demo users

| User | Role(s) | Scope |
|---|---|---|
| `cara` | Control engineer | all |
| `sam` | Security approver | all |
| `eli` | Cloud engineer | all |
| `max` | Security approver + Cloud engineer (shows separation of duties) | all |
| `ada` | Admin (no approval authority) | all |
| `vic` | Viewer / auditor | all |
| `riley`, `pat`, `dana`, `lee` | Exception requester + viewer | retail / payments / analytics / sandbox scopes |

See `docs/authorization-matrix.md`.

## Demo walkthrough (about 10 minutes)

1. **cara** → *Controls* → `CTL-AZ-SEARCH-PNA`. The *Next required decision* box drives the flow. Read the
   intent, prevention boundary and limitations; note the existing **Audit-only** assignment in the baseline
   (externally managed, flagged as a possible overlap).
2. Submit revision 1, then **Validate** and **Submit** the built-in assignment implementation (verified
   definition `ee980b6d-…`, version 1.0.1, digest-pinned).
3. **Run assessment** at `az-mg-contoso`: 8 resources = 1 compliant + 5 non-compliant + 1 unknown + 1 not
   applicable. Exceptions (pending, approved-but-unapplied, effective, expired) are counted separately.
   Request impact covers only the 7 supplied requests. Click any count to see its rows.
4. Resolve the pilot blocker: *Record readiness evidence* (application `retail-catalog`, scope
   `az-sub-retail-prod`, `Client connectivity validation`, satisfied), then run the assessment again.
5. *Create plan* (pilot suggested: `az-sub-retail-prod`), then *Submit change package*. All pre-approval gates
   pass; the approved retail-partner exception becomes an exemption addition with its expiry consequence.
6. Switch to **max** and approve as security, then try to approve again as Cloud Engineering: refused
   (one identity, one approval). Switch to **eli** and approve as Cloud Engineering. The package is approved.
7. As **eli**: *Export approved handoff bundle* (open it: deterministic files, banner, ROLLBACK, PR text),
   *Advance* to Observation (reuses the existing audit assignment) and to Pilot, *Run mock pipeline*
   (targets become Applied, not Verified), then *Record fixture observation* on each target → Verified.
   Coverage shows **1/7 verified protected** with 2 effective exemptions kept in the denominator.
8. Try the failure paths: mock pipeline `FAILURE`/`STALE`/`MISMATCH`, observation `DRIFT` (Drifted + follow-up
   work, no correction), edit the plan after approval (package goes Stale and needs re-approval).
9. `CTL-AWS-S3-PUBLIC-ACCESS`: run the assessment at `aws-root`. Payments has an insecure baseline
   (`PREREQUISITE_INSECURE`), sandbox is unknown, the inherited Prod-OU deny already blocks REQ-AWS-7, and
   the bucket exception requires a different mechanism. Creating a plan ring with `{"effect": "Audit"}` through
   the API (`POST /api/v1/rollout-plans`) is refused with `UNSUPPORTED`: SCPs have no audit mode. Packages are blocked as **demo-only** (AWS docs not verified from a primary source).
10. **ada** → `POST /api/v1/admin/reconcile` (or `make reconcile`): expired exceptions are reconciled; the
    Azure one gets a cleanup item (native expiry already stopped it), the AWS one a removal handoff (no
    native expiry). A second run creates nothing. **Dashboard** and **Audit** show the results.

## Example API calls

```bash
B=http://localhost:8000/api/v1
TOKEN=$(curl -s -X POST $B/auth/dev-login -H 'content-type: application/json' -d '{"username":"cara"}' | jq -r .token)
H="authorization: Bearer $TOKEN"

curl -s -H "$H" $B/controls | jq '.items[] | {id, latest_status}'
curl -s -H "$H" -X POST $B/control-revisions/crev-az-search-pna-1/submit -H 'content-type: application/json' -d '{"expected_lock_version":1}'
curl -s -H "$H" -X POST $B/implementation-revisions/irev-az-search-pna-1/validate | jq '{outcome, evaluator_id, integration_ready}'
curl -s -H "$H" -X POST $B/assessments -H 'content-type: application/json' \
  -d '{"implementation_revision_id":"irev-az-search-pna-1","target_scope_id":"az-mg-contoso"}' | jq '.rollup.configuration'
curl -s -H "$H" "$B/assessments/<run_id>/results?configuration_result=UNKNOWN" | jq '.items[].subject_name'
curl -s -H "$H" "$B/rollout-plans/template?control_id=CTL-AZ-SEARCH-PNA&implementation_revision_id=irev-az-search-pna-1"
curl -s -H "$H" -X POST $B/rollout-plans/<plan_id>/packages -H 'content-type: application/json' -d '{"through_stage":"PILOT"}'
# approvals (as sam and eli), bound to the digest you reviewed:
curl -s -H "authorization: Bearer $SAM" -X POST $B/change-packages/<pkg_id>/decisions -H 'content-type: application/json' \
  -d '{"decision":"APPROVE","role":"SECURITY_APPROVER","expected_digest":"sha256:…","rationale":"Reviewed intent and exceptions"}'
curl -s -H "authorization: Bearer $ELI" -X POST $B/change-packages/<pkg_id>/export
curl -s -H "authorization: Bearer $ELI" -X POST $B/mock-pipeline/runs -H 'content-type: application/json' \
  -d '{"bundle_id":"<bundle_id>","ring":"PILOT","scenario":"SUCCESS"}'
curl -s -H "authorization: Bearer $ELI" -X POST $B/mock-observations -H 'content-type: application/json' \
  -d '{"delivery_target_id":"<target_id>","scenario":"MATCH"}'
curl -s -H "$H" $B/dashboard/metrics | jq '.coverage_pairs'
curl -s -H "$H" "$B/audit?control_id=CTL-AZ-SEARCH-PNA" | jq '.items[].action'
```

Errors are structured: `{"error": {"code", "message", "details", "correlation_id"}}`. Lists are paginated
(`limit`, `offset`). Send `X-Correlation-ID` to trace a request through the audit log.

## Optional: AI Control Workspace (A2UI)

An optional second experience next to the unchanged Classic Experience. It answers investigation questions
by composing an interactive canvas (A2UI v0.9, `@a2ui/react` 0.11.1) from an approved component catalog,
using the same services, authorization and evidence as Classic. It changes nothing except through the
existing governed workflows after you confirm. Details: `docs/a2ui-workspace.md`.

- Enable/disable: `ENABLE_A2UI_WORKSPACE=true|false` (on in `.env.example`, off by default in code). When off,
  the selector and navigation entry are hidden and `/api/v1/workspace/*` returns 404.
- Open it: header selector **Classic / AI Workspace**, the **AI Control Workspace** nav entry,
  `http://localhost:3000/workspace`, or *Investigate in AI Workspace* on a Control Detail page. Your choice is
  saved per user; Classic stays the default.
- Deterministic mode needs no AI credentials. Try (as **cara**):
  *What would happen if we prevented public network access for all Azure AI Search services in production?*
  Before any assessment it reports impact as **unknown** and offers to run the existing assessment (with a
  confirmation); afterwards the canvas shows the persisted results restricted to production.
- AI-assisted mode is optional (`WORKSPACE_AI_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`, model
  `claude-opus-5-5`, refusal fallbacks on by default). The model only reads through authorised tools and
  proposes which views to show; the canvas data always comes from the platform.

```bash
B=http://localhost:8000/api/v1
curl -s -H "$H" $B/features
curl -s -H "$H" -X POST $B/workspace/investigations -H 'content-type: application/json' \
  -d '{"question":"What would happen if we prevented public network access for all Azure AI Search services in production?"}' \
  | jq '{intent: .intent.id, scope: .intent.entities.scope_id, summary: .summary.text, findings: [.findings[] | .kind]}'
```

## Documentation

- `docs/architecture.md`: modules, domain model, flows, tradeoffs, integration seams
- `docs/assumptions.md`: supplied facts vs design assumptions vs decisions needing owner agreement
- `docs/control-feasibility.md`: provider verification (sources, dates, digests), coverage, unsupported behaviour
- `docs/handoff-contract.md`: bundle files, determinism, receipt rules
- `docs/authorization-matrix.md`: roles, scopes, separation of duties, demo users
- `docs/a2ui-workspace.md`: optional AI Control Workspace (reuse, catalog, tools, modes, drafts, safeguards)
- `docs/a2ui-catalog.json`: generated A2UI component catalog (props and data contracts)
- `PROGRESS.md`: what is done, what was verified, next increments

## Known limitations

- Fixture data only: inventory, requests, receipts and observations are fixtures or mock data and labelled as such.
- AWS SCP details were corroborated only through search excerpts (primary docs blocked in the build
  environment), so the AWS implementation is demo-only and cannot reach integration-ready status.
- Two evaluators only (Azure AI Search public network access; the AWS S3 Block Public Access protection
  template). No general policy interpreter; anything else is `UNSUPPORTED`.
- No live readers, PR creation, authenticated pipeline callbacks, production SSO or telemetry. AI assistance
  exists only in the optional workspace and is off unless configured.
- Local demo identities only; production configuration refuses to start.
- Audit history is append-only for the application role; it is not proof against a database administrator.
- Reconciliation is a command with a scheduling seam, not a running scheduler.
- Outcome metrics (recurrence, time to verified coverage, policy-induced incidents, denied operations) are shown as unavailable.
