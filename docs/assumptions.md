# Assumptions, supplied facts and open decisions

This MVP was built without enterprise meetings, credentials or live access. It keeps three
categories strictly apart. Nothing in the "design assumptions" or "decisions requiring owner
agreement" sections should be read as confirmed enterprise architecture.

## 1. Supplied facts (project context, not independently verified)

| # | Supplied fact | How the MVP uses it |
|---|---|---|
| F1 | AWS Service Control Policies and Azure Policy already exist in the enterprise. | Existing assignments/attachments are modelled as `PolicyBinding` rows with `management=EXTERNALLY_MANAGED`, `origin=EXISTING_IMPORTED` (seeded as fixtures). |
| F2 | Cloud Engineering owns policy deployment and provisioning infrastructure. | The platform never deploys. It exports a GitOps handoff bundle; delivery evidence comes back as (mock) receipts. |
| F3 | Wiz Cloud is being adopted. Defend, runtime sensors, specific APIs, licensed features and production credentials are not assumed. | Wiz appears only as a kind of source evidence (`WIZ_ISSUE`) with illustrative fixture references. No Wiz integration exists. |
| F4 | Cloud Engineering may already be building secure provisioning. Ownership and integration are not agreed. | No provisioning features. Rollout plans describe intent; Cloud Engineering executes. |
| F5 | Other teams own IAM and existing remediation processes. | Remediation, readiness and cleanup are lightweight `WorkReference` links to the owning team; no workflow engine, no IAM/JIT. |
| F6 | Engineering capacity is limited (~two engineers with shared technical leadership). | Modular monolith, one database, synchronous processing, no queues/buses, two provider adapters only. Treated as a design constraint, not a staffing commitment. |
| F7 | Proposed responsibility boundaries (Cloud Security / Cloud Engineering / application teams). | Encoded as roles (see `authorization-matrix.md`) and separate approvals. |

## 2. Design assumptions (conservative defaults made to keep building)

| # | Assumption | Where it lives | Consequence if wrong |
|---|---|---|---|
| A1 | Risk-acceptance authority for exceptions is the `SECURITY_APPROVER` role, with max validity CRITICAL 30d / HIGH 90d / MEDIUM 180d / LOW 365d. | `governance_settings.risk_acceptance` (`confirmed_with_organisation: false`) | Change via `PUT /api/v1/admin/settings/risk_acceptance` (ADMIN, audited). |
| A2 | Evidence freshness: readiness 30 days, observation 24 h, assessment 72 h, inventory 168 h, receipts 24 h. | `governance_settings.evidence_freshness` | Configurable; staleness makes packages stale at export/acceptance. |
| A3 | Enforcing rings must have zero open readiness blockers and zero unknown configurations. | `governance_settings.rollout` | Configurable thresholds, no risk scores. |
| A4 | A change package covers rings up to a chosen stage; later rings need a new reviewed package. | `rollouts.create_package` | Keeps approvals tied to what is actually ready. |
| A5 | Security approval of a package also approves the control revision's intent; Cloud Engineering approval covers implementation and delivery readiness. Both must be distinct identities, neither an author. | `rollouts.decide_package` | If the organisation wants separate intent approval, add a revision-level decision. |
| A6 | Readiness evidence is attested by the named team; the platform runs no network tests. | `readiness_evidence` (provenance `MANUAL`/`FIXTURE`) | Evidence quality depends on teams. |
| A7 | An exemption is applied before the enforcing assignment in the same ring. | Bundle `rollout.json` ordering, deployment instructions | Cloud Engineering's pipeline must honour ordering. |
| A8 | Seed timestamps are relative to the seed time (anchor = current hour) so freshness windows behave sensibly; tests use a fixed anchor and a fixed clock. | `app/seed/seed.py`, `tests/conftest.py` | Re-run `make reset` if you return to the demo days later. |
| A9 | Fixture identifiers (subscription GUIDs of zeros, accounts 111…/222…, `r-fx01`, `ou-fx01-*`, `p-FIXTURE-0001`) are synthetic. Real provider identifiers appear only where verified (see `control-feasibility.md`). | Fixtures | None, they are labelled fixtures. |
| A10 | Bearer tokens (not cookies) are used for local demo identities, so CSRF does not apply; tokens are kept in `sessionStorage`. | `app/auth/tokens.py`, `frontend/src/lib/api.ts` | Production SSO must re-evaluate session handling. |

## 3. Decisions requiring owner agreement

| # | Decision | Proposed owner(s) | Current MVP default |
|---|---|---|---|
| D1 | Who may accept risk for exceptions, per severity, and maximum validity. | Cloud Security leadership + risk/governance function | A1 |
| D2 | Format and location of the GitOps handoff (repository, PR template, CODEOWNERS) and who merges. | Cloud Engineering | Local export directory + deterministic zip |
| D3 | Authenticated pipeline callback contract for receipts (identity, signing, replay protection). | Cloud Engineering | Mock receipts only; `LIVE` rejected |
| D4 | Authoritative inventory and observation sources (Azure Resource Graph / Policy Insights, AWS Config, Wiz) and their freshness SLAs. | Cloud Engineering + Cloud Security | Fixture snapshots |
| D5 | Whether AWS Organizations S3 policies (centrally managed Block Public Access) are enabled or planned; how they interact with the SCP baseline-protection pattern. | Cloud Engineering (AWS Organizations owners) | Recorded as a native alternative only |
| D6 | Break-glass/emergency recovery procedure for policy infrastructure. | Cloud Engineering | Referenced, not implemented; no bypass role here |
| D7 | Ownership lookup (CMDB or tags) for application owners and business owners. | Application portfolio / CMDB owners | Fixture fields; missing owners are blockers |
| D8 | Production identity provider (Microsoft Entra ID / OIDC), group-to-role mapping, scope assignment process. | Identity team | Local demo identities only; non-local startup fails |
| D9 | Where remediation work lives (ticketing system) and how links are exchanged. | Remediation process owners | `WorkReference` rows with optional external refs |
| D10 | Audit retention requirements and external tamper-resistant storage. | Security governance | App-role append-only table; not DBA-proof |
| D11 | Whether pilot/limited/broad ring membership follows environments, business units or management groups/OUs. | Cloud Engineering + Cloud Security | Template suggests the subscription/account with the fewest blockers |
