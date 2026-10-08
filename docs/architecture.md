# Architecture

## Shape

A modular monolith sized for roughly two engineers:

```
browser ──> Next.js (App Router, client pages) ──/api proxy──> FastAPI ──> PostgreSQL
                                                     │
                                                     ├─ provider adapters (Azure Policy, AWS SCP)
                                                     └─ handoff adapter (local export) + mock pipeline
```

- **Frontend** (`frontend/`): Next.js 14, React 18, TypeScript, Tailwind. Every page reads backend APIs
  through a same-origin route handler (`src/app/api/[...path]/route.ts`) that forwards to `BACKEND_URL`.
  No numbers are hardcoded; all actions are backend commands.
- **Backend** (`backend/app/`):
  - `core/`: settings (fail-closed validation), testable clock, canonical JSON digests, errors, DB session.
  - `auth/`: signed demo tokens, `Principal` with scope-aware role checks.
  - `models/`: SQLAlchemy schema and enums.
  - `providers/`: capability contract (`base.py`), `azure_policy.py`, `aws_scp.py`, verified source records
    (`verification.py`), and `handoff.py` (local export adapter, deterministic zip). No cloud mutation method exists.
  - `services/`: domain modules: `controls`, `implementations`, `assessments`, `exceptions`, `rollouts`
    (plans, packages, gates, approvals, progression), `handoffs` (bundles, receipts, observations),
    `reconcile`, `dashboard`, `control_detail`, `audit`, `settings`, `scopes`.
  - `api/`: thin REST routers, strict Pydantic request models, middleware (correlation ids, size/type limits,
    security headers), dependency wiring.
  - `seed/`: deterministic fixtures and seeding; `cli.py`: migrate/seed/reset/reconcile/check-config.
- **Database**: PostgreSQL 16 with Alembic migrations. Migration 0002 adds immutability triggers and grants.

## Domain model (summary)

| Concept | Table(s) | Key properties |
|---|---|---|
| Security intent | `controls`, `control_revisions` | Provider-independent; revisions immutable after submission (DB trigger); lifecycle DRAFT → IN_REVIEW → APPROVED → SUPERSEDED/RETIRED. Origin PROPOSED/IMPORTED/EXTERNALLY_MANAGED; operational owner never transferred by cataloguing. |
| Mechanism | `implementations`, `implementation_revisions`, `implementation_validations` | Provider, policy kind, source ref, pinned version, content digest, parameters, assignment settings, native document, capabilities, prerequisites, limitations, server-decided verification. Validations are append-only and bound to a revision digest. |
| Actual assignment | `policy_bindings` | Separate from definitions; target scope, settings, exclusions, desired vs observed state, observation time, provenance. Audit at one scope and Deny at another coexist. |
| Scope | `scopes` | AWS root/OU/account, Azure MG/subscription/RG; explicit ancestry (`ScopeTree`). |
| Inventory | `inventory_snapshots`, `resource_snapshots`, `request_fixture_sets` | Immutable, digested fixture snapshots with missing-field tracking. |
| Readiness evidence | `readiness_evidence` | Append-only; newest per prerequisite wins; freshness derived on read. |
| Assessment | `assessment_runs`, `assessment_results` | Exact revisions, snapshot and fixture ids, bindings/exceptions/evidence snapshot, evaluator version, input/result digests, rollups, blockers, confidence categories. Immutable. |
| Exceptions | `exceptions`, `approval_decisions` | Governance status vs native status; representability; lineage/renewal; validity derived from the clock. |
| Rollout | `rollout_plans` | Rings, stage (ASSESSMENT → BROAD), state (ACTIVE/PAUSED/CANCELLED), rollback plan. |
| Change package | `change_packages`, `approval_decisions` | Immutable canonical manifest + digest (DB trigger); approvals bound to the digest and stored separately. |
| Handoff | `handoff_bundles`, `delivery_targets` | Deterministic files and digests; per-target delivery state. |
| Delivery evidence | `deployment_receipts`, `observations` | Mock/fixture provenance; validation status; history preserved. |
| Follow-up | `work_references` | Lightweight links to other teams' work; deduplicated. |
| Audit | `audit_events` | Append-only through the app role; written in the same transaction as the change. |

## Key flows

1. **Assessment** (`services/assessments.py`): select evaluator strictly → evaluate snapshot resources →
   derive exception dispositions from the clock → readiness from evidence → baseline from existing bindings
   → evaluate request fixtures → reconcile counts → persist run, then results.
2. **Package** (`services/rollouts.py`): build canonical manifest from current state (control revision,
   implementation revision, deploy rings, exception additions/removals/expiry consequences, baseline digest,
   assessment refs, rollback) → digest → evaluate gates. Approvals re-run gates in the same transaction.
   `manifest_current` rebuilds the manifest to detect material change.
3. **Export / acceptance** re-run all gates; failures mark the package STALE without changing its bytes.
4. **Receipts** validate provenance, idempotency, bundle digest, ring authorization, scope, config digests,
   freshness and ordering. **Observations** compare expected vs observed via the provider; only fresh matches
   after a valid APPLIED receipt verify.
5. **Reconcile** (`services/reconcile.py`): idempotent transitions and follow-up work only.

## Tradeoffs

- **One package per set of rings** rather than per ring: fewer approvals for the pilot while later rings
  still need a new reviewed package once their blockers clear.
- **Strict evaluator selection** over a general policy interpreter: unsupported documents never pass, at the
  cost of needing a reviewed evaluator for each new template.
- **Manifests rebuilt for staleness detection**: simple and exact, but any new assessment run (even with
  identical results) makes an approved package stale, which is intentional (assessment refs are part of the digest).
- **Synchronous processing**: fixture assessments and exports are small; no queue. Reconciliation is a CLI
  command with a scheduling seam.
- **Bearer tokens + sessionStorage** for local identities: avoids CSRF; production SSO is a separate increment.
- **Triggers + grants for immutability**: protects against application bugs and app-role misuse, not against a DBA.

## Integration seams (defined only where the workflow needs them)

| Seam | Today | Future |
|---|---|---|
| Inventory source | JSON fixtures → snapshots | Azure Resource Graph, AWS Config, Wiz |
| Risk evidence | `source_evidence` entries | Wiz issues/findings references |
| Ownership lookup | Fixture fields; missing owners are blockers | CMDB/tags |
| External work reference | `work_references` with optional `external_ref` | Ticketing integration |
| Artifact handoff | `LocalExportAdapter` | Pull-request adapter (owner agreement required) |
| Pipeline callback | Mock receipts (`provenance=MOCK`) | Authenticated callbacks (`LIVE` rejected today) |
