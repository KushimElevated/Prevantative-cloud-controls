"""Regression tests for workspace hardening: link safety, catalogue matching, model overrides, audit visibility,
evidence redaction for scoped users and server-side draft blockers."""

from __future__ import annotations

import re

import pytest

from app.workspace.a2ui import A2uiValidationError, build_surface, validate_messages
from app.workspace.ai.scripted import ScriptedProvider
from app.workspace.catalog import SAFE_HREF
from journey import AZ_CONTROL, AZ_IMPL_REV, assess
from test_workspace import QUESTION, investigate, views
from test_workspace_ai import plan


@pytest.fixture
def ws(app, api):
    app.state.settings.enable_a2ui_workspace = True
    return api


@pytest.mark.parametrize("href,ok", [
    ("/controls/CTL-AZ-SEARCH-PNA", True), ("/assessments/asmt_1.2", True), ("/exceptions?control_id=CTL-A", True),
    ("/controls/../admin", False), ("/controls/./x", False), ("/controls/..", False), ("//evil.example", False),
    ("javascript:alert(1)", False), ("https://evil.example", False), ("/api/v1/admin/reconcile", False),
])
def test_safe_href_rejects_dot_segments_and_foreign_targets(href, ok):
    assert bool(re.match(SAFE_HREF, href)) is ok


def test_validator_rejects_dot_segment_links():
    data = {"items": [], "counts_by_status": {}, "note": "n", "links": [{"label": "x", "href": "/controls/../audit"}]}
    msgs = build_surface("s1", [{"component": "ExceptionReview", "title": "Exceptions", "view": "exceptions"}],
                         {"exceptions": data})
    with pytest.raises(A2uiValidationError):
        validate_messages(msgs)


def test_one_coincidental_word_does_not_select_a_control(ws):
    out = investigate(ws, "cara", "Should we prevent quantum entanglement in mainframes?")
    assert out["intent"]["entities"]["control_id"] is None
    assert any(a["id"] == "prepare_control_draft" for a in out["context"]["actions"])


def test_control_named_in_question_is_not_overridden_by_the_model(app, ws):
    app.state.workspace_ai_provider = ScriptedProvider([], plan(control_id="CTL-AWS-S3-PUBLIC-ACCESS"))
    out = ws.post("cara", "/workspace/investigations",
                  {"question": f"What is the impact of {AZ_CONTROL} in production?", "mode": "ai"},
                  expected=200).json()
    assert out["mode"] == "ai"
    assert out["intent"]["entities"]["control_id"] == AZ_CONTROL
    assert views(out)["control"]["control_id"] == AZ_CONTROL
    assert "Kept CTL-AZ-SEARCH-PNA" in (out["mode_note"] or "")


def test_ai_audit_without_scope_is_never_catalogue_level(app, ws):
    # dana reads only analytics-dev (Azure); an AWS question leaves no readable scope.
    app.state.workspace_ai_provider = ScriptedProvider([], plan(control_id=None, scope_id=None))
    out = ws.post("dana", "/workspace/investigations",
                  {"question": "Is the AWS S3 public access control ready to roll out?", "mode": "ai"},
                  expected=200).json()
    assert out["intent"]["entities"]["scope_id"] is None
    events = ws.get("cara", "/audit?action=workspace.ai_investigation", expected=200).json()["items"]
    assert events and events[0]["scope_ids"] == ["az-rg-analytics", "az-sub-analytics-dev"]
    for user in ("riley", "pat", "lee"):
        seen = ws.get(user, "/audit?action=workspace.ai_investigation", expected=200).json()["items"]
        assert seen == [], user


def test_scoped_user_does_not_receive_run_level_text(ws):
    assess(ws, "az-mg-contoso")
    ev = views(investigate(ws, "riley"))["evidence"]
    assert ev["disclosure"] is None and ev["confidence"] is None and ev["assumptions"] == []
    assert ev["limitations"] and "hidden" in ev["limitations"][0]
    full = views(investigate(ws, "cara"))["evidence"]
    assert full["disclosure"] and full["limitations"] and full["assumptions"]


def test_rollout_draft_blockers_are_enforced_on_submit(ws):
    draft = ws.post("cara", "/workspace/drafts/prepare", {"kind": "rollout_plan", "params": {
        "control_id": AZ_CONTROL, "implementation_revision_id": AZ_IMPL_REV}}, expected=200).json()
    assert draft["can_submit"] is False and draft["basis"]["assessment_run_id"] is None
    r = ws.post("cara", "/workspace/drafts/submit", {"kind": "rollout_plan", "basis": draft["basis"],
                                                     "payload": draft["payload"], "confirmed": True},
                expected=409).json()
    assert r["error"]["code"] == "DRAFT_BLOCKED"
    assert ws.get("cara", f"/rollout-plans?control_id={AZ_CONTROL}", expected=200).json()["total"] == 0


def test_control_draft_without_provider_or_type_cannot_be_submitted(ws):
    draft = ws.post("cara", "/workspace/drafts/prepare", {"kind": "control", "params": {
        "problem_statement": "Something should never be publicly reachable."}}, expected=200).json()
    assert draft["can_submit"] is False
    assert any("provider" in b for b in draft["blocking_reasons"])


def test_headline_question_still_matches_after_stopwords(ws):
    out = investigate(ws, "cara", QUESTION)
    assert out["intent"]["entities"]["control_id"] == AZ_CONTROL
    assert out["intent"]["entities"]["scope_id"] == "az-mg-prod"
