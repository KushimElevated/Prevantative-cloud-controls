# Authorization matrix

Enforced server-side (`backend/app/auth/principal.py`, service functions). Roles and scopes are loaded from
the database for every request; nothing is trusted from client headers or forms. A role granted at a scope
applies to that scope and its descendants; a role without a scope applies everywhere.

| Capability | VIEWER | CONTROL_ENGINEER | EXCEPTION_REQUESTER | SECURITY_APPROVER | CLOUD_ENGINEER | ADMIN |
|---|---|---|---|---|---|---|
| Read controls/implementations (catalogue) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Read scoped data (resources, results, exceptions, bindings, deliveries, plans) | own scopes | own scopes | own scopes | own scopes | own scopes | own scopes |
| Author control/implementation revisions, validate, submit, delete unreferenced drafts | | ✓ | | | | |
| Run assessments, route blockers | | ✓ | | | | |
| Record readiness evidence | | ✓ (scope) | ✓ (scope) | | ✓ (scope) | |
| Request / renew exceptions | | | ✓ (scope) | | | |
| Start review, approve/reject (risk acceptance), revoke exceptions | | | | ✓ (scope, not own request) | | |
| Create/edit rollout plans, submit change packages | | ✓ | | | | |
| Security approval of a package | | | | ✓ (global, not an author) | | |
| Cloud Engineering approval of a package | | | | | ✓ (global, not an author) | |
| Export approved bundle / draft export | | ✓ | | | ✓ | |
| Advance rollout | | | | | ✓ (global) | |
| Pause / resume / cancel rollout | | | | ✓ | ✓ | |
| Record mock receipts, run mock pipeline, record fixture observations | | | | | ✓ (global) | |
| Run reconciliation | | | | | ✓ | ✓ |
| Change governance settings, grant/revoke roles | | | | | | ✓ (never for self) |
| Retire a control | | | | ✓ | | |

## Separation of duties (independent of roles held)

- Package approvals require a `SECURITY_APPROVER` decision **and** a `CLOUD_ENGINEER` decision by **two
  different identities**, bound to the exact `manifest_digest`.
- Authors are excluded: the package submitter, plan creator, and the creators/submitters of the included
  control and implementation revisions cannot approve or reject.
- One identity that holds both approver roles can record only one decision per package.
- Exception requesters cannot decide their own requests.
- ADMIN has no approval authority, cannot grant itself roles, and does not bypass any gate.
- Every gate is evaluated in the backend inside the same transaction as the decision. There is no generic
  status `PATCH`; transitions are explicit commands.
- Denied commands (403/409) are audited in their own transaction (`command.denied`).

## Demo identities

| User | Roles | Scope |
|---|---|---|
| `cara` | CONTROL_ENGINEER, VIEWER | all |
| `sam` | SECURITY_APPROVER, VIEWER | all |
| `eli` | CLOUD_ENGINEER, VIEWER | all |
| `max` | SECURITY_APPROVER, CLOUD_ENGINEER | all (multi-role SoD demo) |
| `ada` | ADMIN | all |
| `vic` | VIEWER | all (auditor) |
| `riley` | EXCEPTION_REQUESTER, VIEWER | `az-sub-retail-prod`, `aws-acct-retail` |
| `pat` | EXCEPTION_REQUESTER, VIEWER | `az-sub-payments-prod`, `aws-acct-payments` |
| `dana` | EXCEPTION_REQUESTER, VIEWER | `az-sub-analytics-dev` |
| `lee` | EXCEPTION_REQUESTER, VIEWER | `az-sub-sandbox`, `aws-acct-sandbox` |

Demo login works only with `AUTH_MODE=local-demo` and `APP_ENV` local/test. Any other configuration fails
at startup until a real identity provider is implemented.
