# GitOps handoff contract (v1)

The platform produces a **reviewable change-control package** that Cloud Engineering can accept.
It never deploys. Approved or exported does not mean deployed.

## Bundle kinds

| Kind | Produced by | Status banner | Deployable |
|---|---|---|---|
| `DRAFT_UNAPPROVED` | `POST /api/v1/change-packages/{id}/draft-export` | `DRAFT - UNAPPROVED - NOT FOR DEPLOYMENT.` | No; receipts referencing it are `REJECTED_NOT_AUTHORIZED`. |
| `APPROVED_HANDOFF` | `POST /api/v1/change-packages/{id}/export` | `APPROVED FOR HANDOFF - not deployed.` (or `APPROVED BUT DEMO-ONLY` when the implementation is not verified against primary documentation) | Yes, by Cloud Engineering's pipeline. |

Export of an approved package re-runs every gate (approval validity, exception windows, evidence and
assessment freshness, baseline digest, material change). A failure marks the package `STALE` and refuses
the export, even if the manifest bytes are unchanged.

## Determinism

Files are rendered with sorted keys and fixed formatting, contain no export timestamp, and the zip uses fixed
entry timestamps and ordering. The same approved package always yields the same files, the same
`bundle_digest` and the same zip bytes (tested).

`bundle_digest = sha256(canonical_json(sorted([path, sha256(content)] for each file)))`.

## Files

| Path | Content |
|---|---|
| `manifest.json` | Schema `ccp.handoff-bundle/v1`, bundle kind, banner, demo-only flag, package id/revision, `change_manifest_digest`, full change manifest, per-file digests. |
| `native/azure/policy-assignment-<scope>.json` | Assignment referencing the built-in definition id; effect parameter; enforcementMode; notScopes; non-compliance message; metadata with control/implementation revision and pinned definition version/digest. |
| `native/azure/policy-exemption-<exception>-<hash>.json` | Exemption referencing the new assignment; `exemptionCategory`; `expiresOn` (native expiry); metadata (requester, approver, risk owner). |
| `native/aws/scp-attachment-<scope>.json` | SCP name/type/description, `TargetId`, `PolicyContentDigest`, policy `Content`. Policy id is assigned by AWS Organizations and must be reported in the receipt. |
| `parameters.json` | Implementation parameters, ring settings, intended changes with config digests. |
| `scopes.json` | Target scope path and resolved descendants per ring scope. |
| `baseline-comparison.json` | Existing bindings (desired/observed digests) and the assessment's baseline-vs-proposed summary. |
| `assessment-summary.json` | Disclosure, rollup, confidence, blockers, limitations, assumptions, validation checks/tests, prerequisites. |
| `exceptions.json` | Additions, renewals, removals, expiry consequences, exemption artifacts, native-expiry support. |
| `approvals/attestations.json` + `REVIEWERS.md` | Decisions bound to the manifest digest (stored outside the manifest to avoid circular hashing); reviewer instructions. |
| `rollout.json` | Rings, entry gates, pause criteria, ordering (exemptions before enforcement). |
| `ROLLBACK.md` | Prior known-good, steps, limitations, emergency path (Cloud Engineering). Rollback is a reviewed change; it does not undo application changes. |
| `PR_DESCRIPTION.md` | Proposed pull-request description (PR creation is deferred). |
| `receipt-schema.json` | JSON Schema for deployment receipts. |
| `README.md` | Banner and pointers. |

## Deployment receipt (v1)

```json
{
  "receipt_id": "unique-idempotency-key",
  "provenance": "MOCK",
  "bundle_digest": "sha256:…",
  "ring": "PILOT",
  "target_scope_native_id": "/subscriptions/00000000-0000-0000-0000-000000000101",
  "pipeline_run_ref": "pipeline run reference",
  "result": "ACCEPTED | APPLIED | FAILED",
  "applied_native_identities": [{"native_identity": "…/policyAssignments/ccp-…", "config_digest": "sha256:…", "native_policy_id": null}],
  "started_at": "2026-10-08T12:00:00Z",
  "completed_at": "2026-10-08T12:05:00Z",
  "error": null
}
```

Processing rules (all tested):

| Situation | Outcome | State change |
|---|---|---|
| `provenance: LIVE` | 422 `UNSUPPORTED` | none |
| Same `receipt_id`, same payload | Returns the original (`duplicate: true`) | none |
| Same `receipt_id`, different payload | 409 `RECEIPT_CONFLICT` | none |
| Unknown bundle digest / draft bundle | `REJECTED_DIGEST_MISMATCH` / `REJECTED_NOT_AUTHORIZED` | none (follow-up work for mismatches) |
| Ring beyond the plan's current stage, or plan cancelled | `REJECTED_NOT_AUTHORIZED` | none |
| No target at that scope/ring | `REJECTED_SCOPE_MISMATCH` | none, follow-up work |
| Reported config digest differs | `REJECTED_DIGEST_MISMATCH` | none, follow-up work |
| Completed outside freshness window, or package fails acceptance recheck | `STALE` (package marked STALE) | none |
| Older than the target's latest state | `OUT_OF_ORDER` | none (history kept) |
| Valid `ACCEPTED` / `APPLIED` / `FAILED` | `VALID` | `ACCEPTED` / `APPLIED` / `FAILED` (+ work item) |

A receipt never sets `VERIFIED`. A target becomes `VERIFIED` only when a fresh observation (within
`observation_max_age_hours`) matches the approved artifact and is not older than the APPLIED receipt.
A later mismatch or missing observation sets `DRIFTED` and opens follow-up work; nothing is corrected
automatically. In this MVP observations are generated from fixture scenarios (`MATCH`, `DRIFT`, `MISSING`) and labelled `FIXTURE`.
