# AI Control Workspace (optional, A2UI-powered)

The AI Control Workspace is an optional second experience next to the unchanged **Classic Experience**.
It answers investigation questions ("What would happen if we prevented public network access for all
Azure AI Search services in production?") by composing an interactive canvas from an approved component
catalog. It is a **presentation and interaction layer**: it owns no domain state, decides nothing, and
changes nothing except through the existing governed workflows after a person confirms.

> Off by default in code (`ENABLE_A2UI_WORKSPACE=false`); `.env.example` turns it on for the local demo.
> With the flag off, every `/api/v1/workspace/*` endpoint returns 404, the navigation entry and selector
> are hidden, and the Classic Experience behaves exactly as before.

## What was reused (integration points)

| Need | Existing piece reused | How |
|---|---|---|
| Identity, roles, scopes | `get_ctx` / `Principal.readable_scope_ids`, `has_role_at` | Every workspace request runs as the caller; every row is filtered with the same readable-scope rule as the Classic API. |
| Control, implementations, next decision | `services.control_detail.control_detail` | `GetControlDetails` / `GetImplementations` read its output. |
| Impact numbers | Persisted `assessment_runs` / `assessment_results` (`services.assessments`) | Never recomputed. A run at an ancestor scope is shown restricted to the selected scope by counting its persisted result rows, labelled as such. |
| Running an assessment | `POST /api/v1/assessments` → `assessments.run_assessment` | Called by the browser after a confirmation dialog. Never called by the workspace server or a model. |
| Coverage | `services.dashboard.resource_coverage` | Grouped per subscription/account for the matrix. |
| Exceptions | `services.exceptions.exception_view` / `disposition` | Governance vs native status unchanged. |
| Rollout, package, gates, bundles | `rollouts.latest_package`, `package_decisions`, stored `gate_results`, `handoff_bundles` | Read-only views; approvals and export stay in Classic. |
| Rollout template | `rollouts.plan_template` | Basis of the rollout draft. |
| Creating plans, exceptions, controls | `rollouts.create_plan`, `exceptions.request_exception`, `controls.create_control` with the existing strict request models | Draft submission calls these functions unchanged (same authorization, validation, audit). |
| Audit history | `services.audit.query_events` (extracted from the existing `/audit` route, behaviour unchanged) | `GetAuditHistory`. |
| Preferences | `users` table | Migration `0003` adds one JSONB column `users.preferences`; no new tables. |
| Design system | `frontend/src/components/ui.tsx`, Tailwind tokens | Canvas components and panels reuse them. |

No new domain tables, no copies of authoritative data, no parallel workflow engine.

## Experience selector and navigation

- `GET /api/v1/features` reports whether the workspace (and AI-assisted mode) is available.
- `GET/PUT /api/v1/me/preferences` stores `{experience: classic|workspace, workspace_mode: deterministic|ai}`
  on the user (audited as `user.preferences_updated`). `effective_experience` is always `classic` when the
  flag is off.
- The header shows a **Classic / AI Workspace** selector and an **AI Control Workspace** navigation entry
  when enabled. `/` sends users to their preferred experience; Classic remains the default.
- Deep links: Control Detail → `/workspace?control=<id>`; workspace components, findings and the context
  panel link back to Classic pages (`/controls/<id>`, `/assessments/<run>`, `/exceptions/<id>`,
  `/bundles/<id>`, `/audit?control_id=<id>`). Workspace URLs (`?q=&control=&scope=&mode=`) are shareable.

## Layout

| Panel | Contents |
|---|---|
| Investigation (left) | Question box, example questions, Deterministic / AI-assisted switch, summary with its origin, findings labelled **Verified evidence**, **Estimated impact**, **Unknown**, **Recommendation** (Platform or AI), and the investigation trace (which tools ran). |
| Canvas (centre) | The A2UI surface, rendered by `@a2ui/react` from the approved catalog only, inside an error boundary. |
| Context & Actions (right) | Current control, scope and evidence basis, next required decision, data labels (demo data), available actions with reasons when disabled, drafts, and links back to Classic. |

## A2UI compatibility

| Item | Value |
|---|---|
| Protocol | A2UI **v0.9** (`createSurface`, `updateComponents`, `updateDataModel`; adjacency-list components; JSON-pointer data model) |
| Renderer | `@a2ui/react` **0.11.1** with `@a2ui/web_core` **0.11.0** (`/v0_9` entry points), `zod` 3.25.76 (peer dependency) |
| Catalog id | `urn:ccp:a2ui:control-workspace:v1` (custom catalog; the basic catalog is not accepted) |
| Rendering | Client only (`next/dynamic`, `ssr: false`) because the renderer depends on Lit |

Verified behaviour of the pinned renderer: it validates component props against the catalog's zod schemas
and throws on violations, but it **accepts unknown component types**. The client therefore pre-validates
every payload (`frontend/src/components/workspace/catalog/validate.ts`) before handing it to the
`MessageProcessor`, in addition to the server-side validation.

## Component catalog

Domain components carry exactly two props: a plain-text `title` and a data binding `{"path": "/views/<key>"}`.
Their data is placed in the surface data model by the server and validated against a strict contract on
both sides (`backend/app/workspace/catalog.py`, `frontend/src/components/workspace/catalog/contracts.ts`,
generated summary in `docs/a2ui-catalog.json`).

| Component | Shows | Source |
|---|---|---|
| `ControlSummary` | Intent, revision status, prevention boundary, limitations, implementations, next decision | `control_detail` |
| `ControlCoverageMatrix` | Per subscription/account: applicable, compliant, non-compliant, unknown, exemptions, verified protected, unknown/not enforced; denominator stated | persisted results + `resource_coverage` |
| `ImpactAssessment` | Configuration counts (reconciling), exception dispositions, request impact (estimate or UNKNOWN), readiness, newly preventive resources, confidence, basis caveats; or "unknown" with a confirmed run action | persisted assessment run |
| `ResourceImpactTable` | Per-resource results with filter and "Prepare exception draft" | persisted results |
| `ApplicationReadinessPanel` | Readiness by application, blockers with owners, supplied evidence and staleness | results, blockers, `readiness_evidence` |
| `PolicyDiffViewer` | Existing bindings (baseline) vs proposed implementation revision, field changes | `policy_bindings`, implementation revision |
| `ScopeSelector` | Readable scopes only; changing it re-runs the investigation | scope tree + principal |
| `ExceptionReview` | Governance/native status, disposition, representability, expiry | exceptions service |
| `RolloutTimeline` | Ring stages of the active plan, or that none exists | rollout plans |
| `ApprovalStatus` | Package status, digest-bound decisions, failing gates; approvals happen in Classic | packages, decisions |
| `EvidencePanel` | Sources with provenance, freshness and digests; confidence; limitations; demo-data label | snapshots, fixtures, runs, bindings |
| `GitOpsHandoffPreview` | Exported bundle kind, banner, digest and file digests; "approved does not mean deployed" | handoff bundles |
| `CanvasStack`, `CanvasNotice` | Layout container and plain-text notices | composer |

Client actions a component may emit (anything else is dropped): `ws.select_scope`, `ws.run_assessment`
(confirmation dialog, then the existing assessment endpoint), `ws.prepare_exception_draft`.

## Typed tools

`GET /api/v1/workspace/tools` lists them with their input schemas.

| Tool | Kind | Executed by |
|---|---|---|
| SearchControls, GetControlDetails, GetImplementations, GetCurrentCoverage, GetImpactAssessment, GetApplicableResources, GetApplicationReadiness, GetExceptions, GetRolloutStatus, GetAuditHistory | READ | Workspace server, automatically, as the caller, within readable scopes |
| PrepareControlDraft, PrepareExceptionDraft, PrepareRolloutDraft | DRAFT | Workspace server on the user's request; returns an unsaved proposal |
| RunImpactAssessment | COMMAND | The user's browser, after confirmation, through `POST /api/v1/assessments`; never offered to a model |

Every input is validated with a strict pydantic model (unknown fields and malformed ids are rejected).

## Deterministic mode (no model)

Works without any AI configuration. A small rule set (`backend/app/workspace/intents.py`) picks an intent
from keywords, a provider / native resource type / environment / application from hints, the control from
the catalogue (explicit `CTL-…` id, deep-link context, or best match by resource type, implementation
prerequisites and words; generic words such as "public", "access" or "block" alone never select a control),
and the scope from the scopes the caller can read, in this order: a scope selected in the Scope selector or a
deep link, a scope id named in the question, the widest readable scope matching the environment/application
("pre-production" counts as non-production), else the widest readable scope of the control's provider. A
control's provider overrides a conflicting provider word in the question. If the caller can read no scope of
that provider, every view says so instead of showing another scope's results. Every match is shown in the trace.

| Intent | Example | Canvas |
|---|---|---|
| `impact_preview` | "What would happen if we prevented public network access for all Azure AI Search services in production?" | All 12 components |
| `exception_review` | "What exceptions exist for Azure AI Search public network access?" | Control, scope, exceptions, resources, evidence |
| `rollout_status` | "Is the AWS S3 public access control ready to roll out?" | Control, rollout, approvals, handoff, impact, evidence |
| `readiness` | "Which applications are blocked by missing private endpoint evidence?" | Control, scope, readiness, resources, evidence |
| `coverage_status` | "Which Azure AI Search services in retail production are not compliant?" | Control, scope, coverage, policy diff, resources, evidence |
| `audit_history` | "Who approved changes to CTL-AZ-SEARCH-PNA?" | Control, approvals, evidence |
| `control_overview` | anything else naming a control | Control, scope, impact, coverage, rollout, evidence |

If no completed assessment covers the scope, impact is shown as **UNKNOWN** with an offer to run the
existing assessment (enabled only for `CONTROL_ENGINEER`). Nothing is estimated that was not evaluated.

## AI-assisted mode (optional)

Enable only after the data flow is approved: investigation data the caller may read is sent to the
Anthropic API.

```bash
# .env
ENABLE_A2UI_WORKSPACE=true
WORKSPACE_AI_PROVIDER=anthropic          # the only approved provider
ANTHROPIC_API_KEY=...                    # read by the SDK in the API container only; never logged
WORKSPACE_AI_MODEL=claude-opus-5-5       # default
WORKSPACE_AI_EFFORT=medium               # low | medium | high | xhigh | max
WORKSPACE_AI_REFUSAL_FALLBACKS=true      # server-side refusal fallbacks (see below)
docker compose up -d --build --wait
```

How it works (`backend/app/workspace/ai/`):

1. The deterministic interpreter proposes a control and scope.
2. The model (official `anthropic` Python SDK, manual tool loop) may call **READ tools only**, with
   `strict: true` schemas and `tool_choice: auto`. Each call is validated and executed as the caller, so
   the model sees only what the user may see. Tool results are data; nothing the model writes is executed.
3. The model must finish with a JSON plan (structured output): control id, scope id, summary,
   recommendations, which catalog views to show, suggested actions. The plan is validated strictly: ids must
   be well-formed, exist and be readable, a view outside the catalog is ignored, and a control or scope the
   user named, selected or opened from a deep link is never overridden. Notes about ignored values are fixed
   sentences; text the model wrote is never echoed outside the labelled summary and recommendations.
4. The canvas is built by the same deterministic composer, so **every displayed value is authoritative**.
   Only the summary and recommendations come from the model; they are labelled as AI-generated, rendered as
   plain text, invisible formatting characters (zero-width, bidi overrides) are stripped, and anything that
   reads as a link or address (any `scheme://`, `javascript:`/`mailto:`-style schemes, protocol-relative
   `//host`, `www.` and bare web hosts) is replaced with `[link removed]`.
5. Any failure (not configured, network, refusal, timeout, invalid plan) returns the deterministic
   investigation with a note. Each AI run is audited (`workspace.ai_investigation`: model, tools called,
   outcome; no credentials). The event is scoped to every scope whose data the run read, so it is visible to
   the owners of that data and never catalogue-wide.

Model defaults: `claude-opus-5-5`, adaptive thinking (always on for this model; depth set with
`output_config.effort`), `max_tokens` 16000, one SDK retry, 60 s per call, at most 8 tool calls, and an
overall deadline of 120 s per investigation (`WORKSPACE_AI_DEADLINE_SECONDS`). At most 3 AI investigations
run at once per API process (`WORKSPACE_AI_MAX_CONCURRENT`); extra requests get the deterministic result
immediately. The request's database connection is released while waiting on the model, so slow AI calls
cannot exhaust the pool the Classic Experience uses.
**Refusal fallbacks are enabled by default** (`fallbacks: "default"` with the
`server-side-fallback-2026-07-01` beta): if the model's safety classifiers decline a request (security
topics can trigger this), the API re-runs it on Anthropic's recommended fallback model and the served model
is recorded. Set `WORKSPACE_AI_REFUSAL_FALLBACKS=false` to turn this off.

The model can never deploy, approve, grant exceptions, run assessments or submit drafts: those tools are not
given to it, and every state change requires a person to confirm in the UI and passes the existing
authorization, validation and gates.

## Guided drafts

`POST /api/v1/workspace/drafts/prepare` returns an unsaved draft (exception request, rollout plan or control
proposal) with the **basis** it was prepared from: control revision id and content digest, implementation
revision id and digest, the assessment run (and result digest) it relied on, the active-plan check or the
duplicate-control check. Fields that are security decisions (justifications, risk owner, compensating
controls, severity, objective, prevention boundary) are left for a person to write.

`POST /api/v1/workspace/drafts/submit` requires `confirmed: true`. It checks the caller's role first (the same
403 as the Classic API, before any basis detail is returned), recomputes the basis and refuses with
**409 `DRAFT_STALE`** if anything changed, re-checks the draft's preconditions and refuses with **409
`DRAFT_BLOCKED`** (with the reasons) if they no longer hold, then validates the payload with the existing strict
request model and calls the existing service function. Control proposals collect provider and native resource
type in the form; their duplicate-control check is recomputed on the server from what is submitted. An active
rollout plan the caller cannot read is never named. If a draft is refused as stale, "Prepare again" keeps what
the person already wrote. Change packages, approvals by two independent identities, export,
delivery and verification remain Classic workflows with their existing gates (including `manifest_current`,
which already makes approved packages stale when the evidence changes).

## Safeguards (mapping)

| Safeguard | Where |
|---|---|
| Classic unchanged; no forced switch | Feature flag, Classic default, selector is opt-in |
| No duplicated business logic or data | Tools call existing services; views are not persisted |
| Gates not weakened | Submissions call the same services; packages/approvals untouched |
| No LLM dependency | Deterministic mode; AI optional and isolated in `app/workspace/ai/` |
| No live cloud mutations | No new mutation path; `ENABLE_LIVE_DEPLOYMENT` still refused |
| No unauthorized evidence to the agent | Tools run as the caller with readable-scope filtering |
| No model-generated code executed | Model output is a validated JSON plan and plain text; catalog-only components; client and server validation; no `dangerouslySetInnerHTML` |

## Limitations

- The deterministic interpreter is keyword-based; unusual phrasings fall back to a control overview or a
  catalogue search, shown in the trace.
- AI-assisted mode was exercised with a scripted provider and a stubbed SDK client in tests; it has not
  been run against the live API in this environment (no credentials).
- Investigations are not persisted; deep links re-run them.
- One surface per investigation; no streaming of partial canvases. A view that fails its contract or exceeds
  the size budget is replaced by a notice pointing to Classic; long exception lists are capped (100 items, 10
  resource ids each) with the total shown.

## Adversarial review

The feature was reviewed by four independent lenses (authorization and data exposure; injection and rendering;
governance integrity; correctness and robustness), each followed by a verifier that tried to reproduce or
refute every finding against the running stack and the test database. 22 findings were confirmed (none
critical or high; several duplicates), 4 were refuted. All confirmed findings were fixed and have regression
tests in `backend/tests/test_workspace_review_fixes.py` and `frontend/tests/unit/workspace-review-fixes.test.tsx`.
