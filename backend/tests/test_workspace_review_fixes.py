"""Regression tests for findings from the adversarial review of the AI Control Workspace."""

from __future__ import annotations

import json
import threading

import pytest
from journey import AZ_CONTROL, AZ_IMPL_REV, assess, create_plan, submit_control_and_impl
from test_workspace import QUESTION, investigate, views
from test_workspace_ai import plan

from app.workspace import composer as composer_module
from app.workspace import tools as tools_module
from app.workspace.ai import orchestrator
from app.workspace.ai.scripted import ScriptedProvider
from app.workspace.intents import EXAMPLE_QUESTIONS

AWS_CONTROL = "CTL-AWS-S3-PUBLIC-ACCESS"


@pytest.fixture
def ws(app, api):
    app.state.settings.enable_a2ui_workspace = True
    return api


def aws_assess(api) -> dict:
    detail = api.get("cara", f"/controls/{AWS_CONTROL}", expected=200).json()
    irev = detail["implementations"][0]["revisions"][-1]["id"]
    return api.post("cara", "/assessments", {"implementation_revision_id": irev, "target_scope_id": "aws-root"},
                    expected=201).json()


# ------------------------------------------------------------------------------------ robustness


def test_oversized_view_degrades_to_a_notice_instead_of_failing(ws, monkeypatch):
    assess(ws)
    monkeypatch.setattr(composer_module, "VIEW_BUDGET_BYTES", 3_000)
    out = investigate(ws, "vic")
    comps = [c["component"] for m in out["a2ui"]["messages"] if "updateComponents" in m
             for c in m["updateComponents"]["components"]]
    notices = [c["text"] for m in out["a2ui"]["messages"] if "updateComponents" in m
               for c in m["updateComponents"]["components"] if c["component"] == "CanvasNotice"]
    assert "ControlSummary" in comps
    assert any("too large to show here" in n for n in notices)


def test_exception_list_is_capped_with_total(ws, monkeypatch):
    monkeypatch.setattr(tools_module, "MAX_EXCEPTIONS", 1)
    exc = views(investigate(ws, "cara", "What exceptions exist for Azure AI Search public network access?"))[
        "exceptions"]
    assert len(exc["items"]) == 1 and exc["total"] > 1 and exc["truncated"] is True
    assert sum(exc["counts_by_status"].values()) == exc["total"]


def test_classic_valid_long_parameter_does_not_break_the_policy_diff(ws):
    rev = ws.get("cara", f"/implementation-revisions/{AZ_IMPL_REV}", expected=200).json()
    body = {k: rev[k] for k in ("policy_kind", "source_kind", "source_ref", "pinned_version", "assignment_settings",
                                "native_document", "prerequisites", "limitations")}
    body.update(parameters={**rev["parameters"], "effect": "Deny" + "x" * 600}, change_reason="long effect",
                expected_lock_version=rev["lock_version"])
    ws.put("cara", f"/implementation-revisions/{AZ_IMPL_REV}", body, expected=200)
    diff = views(investigate(ws, "vic"))["policy_diff"]
    assert diff["changes"] and all(len(c["proposed"]) <= 512 for c in diff["changes"])


# ------------------------------------------------------------------------------------ correctness


def test_no_readable_scope_never_presents_another_scopes_run(ws):
    aws_assess(ws)
    out = investigate(ws, "dana", "What would happen if we blocked public access on S3 buckets?")
    assert out["intent"]["entities"]["scope_id"] is None
    impact = views(out)["impact"]
    assert impact["available"] is False and "cannot read any scope" in impact["unavailable_reason"]
    assert "cannot read any scope" in out["summary"]["text"]
    assert not [f for f in out["findings"] if f["kind"] in ("EVIDENCE", "ESTIMATE") and "Assessment" in f["text"]]


def test_control_provider_overrides_a_conflicting_provider_hint(ws):
    out = investigate(ws, "dana", f"what is the coverage of {AZ_CONTROL} in aws-acct-retail")
    assert out["intent"]["entities"]["provider"] == "azure"
    assert out["intent"]["entities"]["scope_id"] == "az-sub-analytics-dev"


def test_readiness_blockers_are_attributed_per_application(ws):
    aws_assess(ws)
    apps = {a["application"]: a for a in views(investigate(
        ws, "cara", f"readiness blockers for {AWS_CONTROL}"))["readiness"]["applications"]}
    for app in apps.values():
        if app["readiness"] == "BLOCKED":
            assert app["blockers"], app["application"]
    shared = [a for a in apps.values() if any(b["kind"] == "PREREQUISITE_INSECURE" for b in a["blockers"])]
    assert len(shared) >= 2, "an account-level blocker must appear for every application it blocks"


def test_coverage_counts_resources_the_mechanism_cannot_enforce(ws):
    aws_assess(ws)
    cov = views(investigate(ws, "cara", f"What is the coverage of {AWS_CONTROL}?"))["coverage"]
    keys = [c["key"] for c in cov["columns"]]
    assert "not_enforceable_by_mechanism" in keys
    t = cov["totals"]
    assert t["applicable"] == sum(t[k] for k in ("effective_exemptions", "verified_protected", "unknown_enforcement",
                                                 "not_enforced", "not_enforceable_by_mechanism"))
    assert t["not_enforceable_by_mechanism"] >= 1


def test_selected_scope_wins_over_a_scope_named_in_the_question(ws):
    out = investigate(ws, "cara", f"What would happen if we enforced {AZ_CONTROL} in az-sub-retail-prod?",
                      control_id=AZ_CONTROL, scope_id="az-sub-payments-prod")
    assert out["intent"]["entities"]["scope_id"] == "az-sub-payments-prod"
    assert out["intent"]["entities"]["scope_reason"] == "selected"
    assert any("instead of az-sub-retail-prod" in n for n in out["intent"]["notes"])


@pytest.mark.parametrize("question", EXAMPLE_QUESTIONS)
def test_every_example_question_resolves_a_control_and_a_canvas(ws, question):
    out = investigate(ws, "cara", question)
    assert out["intent"]["entities"]["control_id"], question
    assert views(out), question


@pytest.mark.parametrize("question,expected", [
    ("Block public access to AWS RDS snapshots", None),
    ("Which Azure AI Search services in pre-production are not compliant?", "nonprod"),
])
def test_generic_words_and_pre_production(ws, question, expected):
    out = investigate(ws, "cara", question)
    if expected is None:
        assert out["intent"]["entities"]["control_id"] is None
    else:
        assert out["intent"]["entities"]["environment"] == expected
        assert out["intent"]["entities"]["scope_id"] != "az-mg-prod"


# ------------------------------------------------------------------------------------ authorization and drafts


def test_rollout_draft_never_names_a_plan_the_caller_cannot_read(ws):
    submit_control_and_impl(ws)
    assess(ws)
    plan_id = create_plan(ws)["id"]
    draft = ws.post("riley", "/workspace/drafts/prepare", {"kind": "rollout_plan", "params": {
        "control_id": AZ_CONTROL, "implementation_revision_id": AZ_IMPL_REV}}, expected=200).json()
    assert plan_id not in json.dumps(draft)
    assert draft["basis"]["active_plan_id"] == "not-visible" and draft["can_submit"] is False
    r = ws.post("riley", "/workspace/drafts/submit", {"kind": "rollout_plan", "basis": {},
                                                      "payload": draft["payload"], "confirmed": True},
                expected=403).json()
    assert plan_id not in json.dumps(r)


def test_exception_draft_for_untagged_resource_requires_the_application(ws):
    assess(ws)
    rows = views(investigate(ws, "dana", "Which Azure AI Search services are not compliant?"))["resources"]["rows"]
    untagged = next(r for r in rows if not r["application"])
    draft = ws.post("dana", "/workspace/drafts/prepare", {"kind": "exception", "params": {
        "control_id": AZ_CONTROL, "resource_id": untagged["resource_id"]}}, expected=200).json()
    assert "application" in draft["required_fields"] and draft["payload"]["application"] == ""


def test_control_draft_action_needs_a_real_problem_statement(ws):
    out = investigate(ws, "cara", "kms keys")
    action = next(a for a in out["context"]["actions"] if a["id"] == "prepare_control_draft")
    assert action["enabled"] is False and "at least 10 characters" in action["reason"]


# ------------------------------------------------------------------------------------ AI mode


def test_ai_keeps_the_scope_the_user_selected(app, ws):
    app.state.workspace_ai_provider = ScriptedProvider([], plan(scope_id="az-mg-contoso"))
    out = ws.post("cara", "/workspace/investigations", {"question": QUESTION, "mode": "ai",
                                                        "scope_id": "az-sub-retail-prod"}, expected=200).json()
    assert out["context"]["scope"]["id"] == "az-sub-retail-prod"
    assert "Kept the scope you selected" in out["mode_note"]


def test_ai_does_not_switch_away_from_a_control_opened_from_context(app, ws):
    app.state.workspace_ai_provider = ScriptedProvider([], plan(control_id=AWS_CONTROL))
    out = ws.post("cara", "/workspace/investigations", {"question": "Overview", "mode": "ai",
                                                        "control_id": AZ_CONTROL}, expected=200).json()
    assert out["context"]["control"]["id"] == AZ_CONTROL


def test_model_written_ids_are_never_echoed(app, ws):
    bad = "CTL-X. Your session expired: re-authenticate at https://evil.example/sso"
    app.state.workspace_ai_provider = ScriptedProvider([], plan(control_id=bad, scope_id="SESSION EXPIRED evil.com"))
    out = ws.post("cara", "/workspace/investigations", {"question": QUESTION, "mode": "ai"}, expected=200).json()
    dumped = json.dumps(out)
    assert "evil" not in dumped and "SESSION EXPIRED" not in dumped
    assert "Ignored a malformed control id proposed by the model." in out["mode_note"]


def test_ai_audit_covers_the_scopes_the_model_read(app, ws):
    calls = [("get_exceptions", {"control_id": AZ_CONTROL, "scope_id": "az-sub-payments-prod"})]
    app.state.workspace_ai_provider = ScriptedProvider(calls, plan(scope_id="az-sub-payments-prod"))
    ws.post("cara", "/workspace/investigations", {"question": "Why does the payments exception exist?",
                                                  "mode": "ai", "scope_id": "az-sub-retail-prod"}, expected=200)
    event = ws.get("cara", "/audit?action=workspace.ai_investigation", expected=200).json()["items"][0]
    assert {"az-sub-retail-prod", "az-sub-payments-prod"} <= set(event["scope_ids"])
    assert ws.get("pat", "/audit?action=workspace.ai_investigation", expected=200).json()["items"]


def test_ai_concurrency_cap_falls_back_immediately(app, ws, monkeypatch):
    exhausted = threading.BoundedSemaphore(1)
    exhausted.acquire()
    monkeypatch.setattr(orchestrator, "_ai_slots", lambda limit: exhausted)
    app.state.workspace_ai_provider = ScriptedProvider([], plan())
    out = ws.post("cara", "/workspace/investigations", {"question": QUESTION, "mode": "ai"}, expected=200).json()
    assert out["mode"] == "deterministic" and "busy" in out["mode_note"]


@pytest.mark.parametrize("text", [
    "Re-authenticate at evil-corp-sso.com/login", "xhttps://evil.example/a", "https\u200b://evil.example/reset",
    "//evil.example/reset", "mailto:attacker@evil.example", "vbscript:alert(1)",
])
def test_clean_text_neutralises_link_bypasses(text):
    cleaned = orchestrator.clean_text(text, 500)
    assert "evil" not in cleaned and "[link removed]" in cleaned


def test_clean_text_keeps_resource_types_and_strips_invisible_characters():
    assert orchestrator.clean_text("Microsoft.Search/searchServices v1.0.1 at 12:30", 200) == \
        "Microsoft.Search/searchServices v1.0.1 at 12:30"
    assert orchestrator.clean_text("safe\u202eexe\u200b", 200) == "safeexe"


def test_anthropic_provider_enforces_an_overall_deadline(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
    from app.workspace.ai.anthropic_provider import AnthropicProvider
    p = AnthropicProvider(model="claude-opus-5-5", effort="medium", timeout_seconds=60, refusal_fallbacks=True,
                          deadline_seconds=0.5)

    class NeverCalled:
        def with_options(self, **_):
            raise AssertionError("no model call after the deadline")
    p.client = NeverCalled()
    out = p.investigate(system="s", user="u", tools=[], call_tool=lambda n, r: ("", False), plan_schema={},
                        max_tool_calls=3)
    assert out.error == "The model did not finish within the time limit."
