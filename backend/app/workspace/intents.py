"""Deterministic question interpretation (no model involved).

A small, explainable rule set: keyword patterns pick an intent, hints pick a provider, a native
resource type and an environment, and the control and scope are resolved against the catalogue and
the scopes the current identity can read. Every match is reported back so the user can see why the
workspace chose what it shows. Unrecognised questions fall back to a control overview or a catalogue
search; nothing is guessed silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models import entities as m
from app.workspace.tools import Investigation, SearchControlsInput, search_controls

FULL = ["ControlSummary", "ScopeSelector", "ImpactAssessment", "ResourceImpactTable", "ApplicationReadinessPanel",
        "ExceptionReview", "PolicyDiffViewer", "ControlCoverageMatrix", "RolloutTimeline", "ApprovalStatus",
        "GitOpsHandoffPreview", "EvidencePanel"]

INTENTS: dict[str, dict[str, Any]] = {
    "impact_preview": {
        "label": "Impact of enforcing a preventive control",
        "patterns": [r"what would happen", r"\bimpact\b", r"\bwhat if\b",
                     r"\bif we (prevent|prevented|block|blocked|deny|denied|enforce|enforced|disable|disabled|"
                     r"turn(?:ed)? off|restrict|restricted)\b", r"\bwould (?:it |this )?break\b", r"\baffected\b"],
        "views": FULL,
    },
    "exception_review": {
        "label": "Exceptions and exemptions",
        "patterns": [r"\bexceptions?\b", r"\bexemptions?\b", r"\bwaivers?\b", r"\brisk acceptance\b"],
        "views": ["ControlSummary", "ScopeSelector", "ExceptionReview", "ResourceImpactTable", "EvidencePanel"],
    },
    "rollout_status": {
        "label": "Rollout, approvals and handoff",
        "patterns": [r"\brollout\b", r"\broll out\b", r"\bdeploy", r"\brings?\b", r"\bpilot\b", r"\bapprov",
                     r"\bpackage\b", r"\bhandoff\b", r"\bbundle\b", r"\bready to\b"],
        "views": ["ControlSummary", "RolloutTimeline", "ApprovalStatus", "GitOpsHandoffPreview", "ImpactAssessment",
                  "EvidencePanel"],
    },
    "readiness": {
        "label": "Application readiness",
        "patterns": [r"\breadiness\b", r"\bready\b", r"\bprivate endpoints?\b", r"\bblockers?\b", r"\bdns\b"],
        "views": ["ControlSummary", "ScopeSelector", "ApplicationReadinessPanel", "ResourceImpactTable",
                  "EvidencePanel"],
    },
    "coverage_status": {
        "label": "Current coverage",
        "patterns": [r"\bcoverage\b", r"\bcovered\b", r"\bprotected\b", r"\bcompliant\b", r"\bnon-?compliant\b",
                     r"\bgaps?\b"],
        "views": ["ControlSummary", "ScopeSelector", "ControlCoverageMatrix", "PolicyDiffViewer", "ResourceImpactTable",
                  "EvidencePanel"],
    },
    "audit_history": {
        "label": "Change history",
        "patterns": [r"\baudit\b", r"\bhistory\b", r"\bwho (?:changed|approved|submitted|created)\b"],
        "views": ["ControlSummary", "ApprovalStatus", "EvidencePanel"],
    },
    "control_overview": {
        "label": "Control overview",
        "patterns": [],
        "views": ["ControlSummary", "ScopeSelector", "ImpactAssessment", "ControlCoverageMatrix", "RolloutTimeline",
                  "EvidencePanel"],
    },
}
INTENT_ORDER = ["impact_preview", "exception_review", "rollout_status", "readiness", "coverage_status",
                "audit_history"]

EXAMPLE_QUESTIONS = [
    "What would happen if we prevented public network access for all Azure AI Search services in production?",
    "Which Azure AI Search services in retail production are not compliant?",
    "What exceptions exist for Azure AI Search public network access?",
    "Is the AWS S3 public access control ready to roll out?",
    "Which applications are blocked by missing private endpoint evidence?",
    "Who approved changes to CTL-AZ-SEARCH-PNA?",
]

RESOURCE_HINTS = [
    (r"\b(?:ai|cognitive) search\b|\bsearch services?\b", "azure", "Microsoft.Search/searchServices"),
    (r"\bstorage accounts?\b|\bblob storage\b", "azure", "Microsoft.Storage/storageAccounts"),
    (r"\bs3\b|\bbuckets?\b", "aws", "AWS::S3::Bucket"),
]
PROVIDER_HINTS = [(r"\bazure\b", "azure"), (r"\baws\b|\bamazon\b", "aws")]
ENVIRONMENT_HINTS = [
    (r"\bnon-?prod(?:uction)?\b", "nonprod"),
    (r"\bprod(?:uction)?\b", "prod"),
    (r"\bdev(?:elopment)?\b", "dev"),
    (r"\bsandbox\b", "sandbox"),
]
NONPROD_ENVS = {"nonprod", "dev", "sandbox"}
CONTROL_ID = re.compile(r"\bCTL-[A-Z0-9-]{3,60}\b")
SCOPE_ID = re.compile(r"\b(?:az|aws)-[a-z0-9-]{2,60}\b")


@dataclass
class Interpretation:
    intent: str
    matched: list[str] = field(default_factory=list)
    provider: str | None = None
    resource_type: str | None = None
    environment: str | None = None
    application: str | None = None
    control_id: str | None = None
    control_reason: str | None = None
    scope_id: str | None = None
    scope_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.intent, "label": INTENTS[self.intent]["label"], "matched": self.matched,
                "entities": {"provider": self.provider, "resource_type": self.resource_type,
                             "environment": self.environment, "application": self.application,
                             "control_id": self.control_id, "control_reason": self.control_reason,
                             "scope_id": self.scope_id, "scope_reason": self.scope_reason},
                "notes": self.notes}


def _intent(q: str) -> tuple[str, list[str]]:
    for name in INTENT_ORDER:
        hits = [m.group(0) for p in INTENTS[name]["patterns"] if (m := re.search(p, q))]
        if hits:
            return name, hits
    return "control_overview", []


def interpret(inv: Investigation, question: str, *, control_id: str | None = None,
              scope_id: str | None = None, intent: str | None = None) -> Interpretation:
    q = question.lower()
    name, hits = (intent, ["selected"]) if intent in INTENTS else _intent(q)
    it = Interpretation(intent=name, matched=hits)
    for pattern, provider in PROVIDER_HINTS:
        if re.search(pattern, q):
            it.provider = provider
            break
    for pattern, provider, rtype in RESOURCE_HINTS:
        if re.search(pattern, q) and (it.provider in (None, provider)):
            it.provider, it.resource_type = provider, rtype
            break
    for pattern, env in ENVIRONMENT_HINTS:
        if re.search(pattern, q):
            it.environment = env
            break

    # Control: explicit id > context > catalogue search.
    explicit = CONTROL_ID.search(question)
    candidate = (explicit.group(0) if explicit else None) or control_id
    if candidate and inv.ctx.session.get(m.Control, candidate) is not None:
        it.control_id = candidate
        it.control_reason = "named in the question" if explicit else "opened from context"
    elif candidate:
        it.notes.append(f"Control {candidate} was not found.")
    if it.control_id is None:
        found = search_controls(inv, SearchControlsInput(query=question[:300], provider=it.provider,
                                                         resource_type=it.resource_type))
        if found["items"]:
            top = found["items"][0]
            it.control_id = top["control_id"]
            it.control_reason = "best catalogue match (" + "; ".join(top["match_reasons"]) + ")"
            others = [i["control_id"] for i in found["items"][1:4]]
            if others:
                it.notes.append("Other candidate controls: " + ", ".join(others))
    if it.control_id is not None:
        rev = inv.control(it.control_id).revisions[-1]
        if it.provider is None and rev.providers:
            it.provider = rev.providers[0]

    # Scope: explicit id > context > environment/application hints > widest readable scope.
    tree = inv.ctx.tree
    explicit_scope = next((s for s in SCOPE_ID.findall(q) if s in tree.scopes), None) or scope_id
    if explicit_scope:
        if explicit_scope in tree.scopes and inv.can_read(explicit_scope):
            it.scope_id = explicit_scope
            it.scope_reason = "named in the question" if explicit_scope != scope_id else "selected"
            it.provider = tree.scopes[explicit_scope].provider
            return it
        it.notes.append(f"Scope {explicit_scope} is not available to your identity.")
    readable = [s for s in tree.scopes.values()
                if (it.provider is None or s.provider == it.provider) and inv.can_read(s.id)]
    apps = {s.application for s in readable if s.application}
    it.application = next((a for a in sorted(apps) if re.search(rf"\b{re.escape(a)}\b", q)), None)
    pool = readable
    if it.environment:
        envs = NONPROD_ENVS if it.environment == "nonprod" else {it.environment}
        pool = [s for s in pool if s.environment in envs]
    if it.application:
        pool = [s for s in pool if s.application == it.application]
    reason = ", ".join(x for x in (f"environment {it.environment}" if it.environment else "",
                                   f"application {it.application}" if it.application else "") if x)
    if not pool and readable:
        it.notes.append("No readable scope matched the environment/application in the question; showing the "
                        "widest scope you can read.")
        pool, reason = readable, ""
    if pool:
        depth = {s.id: len(tree.ancestors(s.id)) for s in pool}
        best = min(pool, key=lambda s: (depth[s.id], s.id))
        it.scope_id = best.id
        it.scope_reason = f"widest readable scope matching {reason}" if reason else "widest readable scope"
        same = [s.id for s in pool if depth[s.id] == depth[best.id] and s.id != best.id]
        if same:
            it.notes.append("Other matching scopes at the same level: " + ", ".join(sorted(same)))
    return it
