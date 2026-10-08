"""AI-assisted workspace mode: read-only tool gate, untrusted plans, deterministic fallback and audit.

The model is replaced by ``ScriptedProvider`` (or a fake Anthropic client), so no network is used. The
canvas must stay authoritative: whatever the model says, displayed values come from the same services
as deterministic mode.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx2
import pytest

import anthropic
from app.core.config import Settings
from app.workspace.a2ui import validate_messages
from app.workspace.ai.anthropic_provider import FALLBACK_BETA, MAX_TURNS, AnthropicProvider
from app.workspace.ai.base import PLAN_SCHEMA, ModelOutcome
from app.workspace.ai.orchestrator import ai_status, clean_text
from app.workspace.ai.scripted import ScriptedProvider
from app.workspace.tools import TOOLS_BY_API_NAME
from app.workspace.tools import TOOLS, tool_specs
from journey import AZ_CONTROL, AZ_IMPL_REV, assess
from test_workspace import QUESTION, RETAIL_PARTNER, action, assert_safe_surface, components, counts_for, views

READ_TOOLS = [t.api_name for t in TOOLS if t.kind == "READ"]
PAYMENTS_MARKERS = ("srch-payments", "payments-kb", "payments-legacy", "000000000102", "az-rg-payments-search")


def plan(**overrides) -> dict:
    return {"control_id": AZ_CONTROL, "scope_id": "az-mg-prod",
            "summary": "Enforcing the control would deny public access for the non-compliant services shown.",
            "recommendations": ["Resolve the readiness blockers before the pilot."],
            "views": ["ImpactAssessment", "ResourceImpactTable"], "suggested_actions": ["run_assessment"],
            **overrides}


class RecordingProvider(ScriptedProvider):
    def investigate(self, **kwargs):
        self.kwargs = kwargs
        return super().investigate(**kwargs)


class FailingProvider(ScriptedProvider):
    """Returns a provider-reported error (e.g. a refusal) instead of a plan."""

    def investigate(self, **kwargs):
        super().investigate(**kwargs)
        return ModelOutcome(final_text=None, model=self.model, stop_reason="refusal",
                            error="The model declined this request.")


@pytest.fixture
def ai(app, api):
    app.state.settings.enable_a2ui_workspace = True

    def run(provider, user: str = "cara", question: str = QUESTION, expected: int = 200, **extra) -> dict:
        app.state.workspace_ai_provider = provider
        return api.post(user, "/workspace/investigations", {"question": question, "mode": "ai", **extra},
                        expected=expected).json()
    run.api = api
    return run


def ai_audit(api, user: str = "cara") -> list[dict]:
    return api.get(user, "/audit?action=workspace.ai_investigation&limit=50", expected=200).json()["items"]


def deterministic(api, user: str = "cara", **extra) -> dict:
    return api.post(user, "/workspace/investigations", {"question": QUESTION, **extra}, expected=200).json()


# ------------------------------------------------------------------------------------ tool gate


def test_provider_receives_only_read_tools(ai):
    provider = RecordingProvider([], plan())
    out = ai(provider)
    assert out["mode"] == "ai" and out["requested_mode"] == "ai"
    assert provider.seen_tools == READ_TOOLS
    assert not {"run_impact_assessment", "prepare_exception_draft", "prepare_rollout_draft",
                "prepare_control_draft"} & set(provider.seen_tools)
    kw = provider.kwargs
    assert kw["plan_schema"] == PLAN_SCHEMA and kw["max_tool_calls"] == 8
    assert "cannot change anything" in kw["system"]
    user = json.loads(kw["user"])
    assert user["question"] == QUESTION and user["suggested_control_id"] == AZ_CONTROL
    assert user["suggested_scope_id"] == "az-mg-prod"
    # Tool schemas are strict-compatible: closed objects with every property required.
    for spec in kw["tools"]:
        schema = spec["input_schema"]
        assert schema["additionalProperties"] is False and schema["required"] == sorted(schema["properties"])
        assert "pattern" not in json.dumps(schema) and "title" not in schema
    assert [t["name"] for t in tool_specs({"READ"})] == READ_TOOLS


@pytest.mark.parametrize("configured,effective", [(0, 1), (3, 3), (500, 20)])
def test_tool_budget_is_bounded(ai, app, configured, effective):
    app.state.settings.workspace_ai_max_tool_calls = configured
    provider = RecordingProvider([("get_control_details", {"control_id": AZ_CONTROL})] * 25, plan())
    ai(provider)
    assert provider.kwargs["max_tool_calls"] == effective and len(provider.results) == effective


def test_non_read_and_unknown_tools_are_rejected_and_nothing_is_persisted(ai):
    assess(ai.api)
    before = counts_for(ai.api)
    calls = [
        ("run_impact_assessment", {"implementation_revision_id": AZ_IMPL_REV, "target_scope_id": "az-mg-contoso"}),
        ("prepare_exception_draft", {"control_id": AZ_CONTROL, "resource_id": RETAIL_PARTNER}),
        ("prepare_rollout_draft", {"control_id": AZ_CONTROL}),
        ("prepare_control_draft", {"problem_statement": "Make a duplicate control please."}),
        ("submit_draft", {"kind": "exception"}),
        ("delete_all_controls", {}),
        ("RunImpactAssessment", {"implementation_revision_id": AZ_IMPL_REV, "target_scope_id": "az-mg-contoso"}),
    ]
    provider = ScriptedProvider(calls, plan())
    out = ai(provider)
    assert [(name, err) for name, _, err in provider.results] == [(name, True) for name, _ in calls]
    for name, text, _ in provider.results:
        assert text == "Error: that tool is not available. Only the read-only tools provided can be used."
    assert counts_for(ai.api) == before
    ai_steps = [s for s in out["steps"] if s["label"].startswith("AI assistant called")]
    # Names the model invents are never echoed; known tool names are shown as they are.
    labels = [name if name in TOOLS_BY_API_NAME else "(unknown tool)" for name, _ in calls]
    assert [(s["tool"], s["status"]) for s in ai_steps] == [(label, "rejected") for label in labels]
    assert out["mode"] == "ai"
    event = ai_audit(ai.api)[0]
    assert event["details"]["tools_called"] == labels


def test_invalid_tool_arguments_are_rejected(ai):
    calls = [
        ("get_control_details", {"control_id": AZ_CONTROL, "include_secrets": True}),
        ("get_control_details", {"control_id": "../../etc/passwd"}),
        ("get_control_details", {}),
        ("get_impact_assessment", {"control_id": AZ_CONTROL, "scope_id": "az-mg-prod' OR 1=1 --"}),
        ("get_audit_history", {"control_id": AZ_CONTROL, "limit": 5000}),
        ("get_applicable_resources", {"control_id": AZ_CONTROL, "configuration_result": "PWNED"}),
        ("search_controls", {"query": "x" * 301}),
        ("get_control_details", {"control_id": AZ_CONTROL}),
    ]
    provider = ScriptedProvider(calls, plan())
    out = ai(provider)
    errors = [err for _, _, err in provider.results]
    assert errors == [True] * 7 + [False]
    assert all(text.startswith("Error: invalid arguments: ") for _, text, err in provider.results if err)
    assert "include_secrets" in provider.results[0][1]
    assert json.loads(provider.results[-1][1])["control_id"] == AZ_CONTROL
    statuses = [s["status"] for s in out["steps"] if s["label"].startswith("AI assistant called")]
    assert statuses == ["rejected"] * 7 + ["ok"]


def test_tools_run_with_the_users_authorization(ai, app):
    assess(ai.api)
    app.state.settings.workspace_ai_max_tool_calls = 20
    scoped = ["get_impact_assessment", "get_applicable_resources", "get_application_readiness", "get_exceptions",
              "get_current_coverage"]
    calls = [(name, {"control_id": AZ_CONTROL, "scope_id": "az-sub-payments-prod"}) for name in scoped]
    calls += [(name, {"control_id": AZ_CONTROL, "scope_id": "az-sub-nope"}) for name in scoped]
    calls += [("get_exceptions", {"control_id": AZ_CONTROL, "scope_id": None}),
              ("get_applicable_resources", {"control_id": AZ_CONTROL, "scope_id": None}),
              ("get_rollout_status", {"control_id": AZ_CONTROL}),
              ("get_audit_history", {"control_id": AZ_CONTROL, "limit": 50})]
    provider = ScriptedProvider(calls, plan(scope_id="az-sub-retail-prod"))
    out = ai(provider, user="riley")
    denied, unknown, readable = provider.results[:5], provider.results[5:10], provider.results[10:]
    assert all(err and text == "Error: Scope az-sub-payments-prod not found" for _, text, err in denied)
    assert all(err and text == "Error: Scope az-sub-nope not found" for _, text, err in unknown)
    assert not any(err for _, _, err in readable)
    resources = json.loads(readable[1][1])
    assert resources["rows"] and {r["scope_id"] for r in resources["rows"]} <= {"az-sub-retail-prod",
                                                                                "az-rg-retail-search"}
    everything = json.dumps([text for _, text, _ in provider.results] + [out]).lower()
    for marker in PAYMENTS_MARKERS:
        assert marker not in everything, marker
    assert out["mode"] == "ai" and out["intent"]["entities"]["scope_id"] == "az-sub-retail-prod"


# ------------------------------------------------------------------------------------ untrusted plan


def test_valid_plan_is_labelled_sanitised_and_canvas_stays_authoritative(ai):
    run = assess(ai.api)
    det = deterministic(ai.api)
    provider = ScriptedProvider(
        [("get_impact_assessment", {"control_id": AZ_CONTROL, "scope_id": "az-mg-prod"})],
        plan(summary="Three services would be denied.\x07 Details: https://evil.example/x and javascript:alert(1)",
             recommendations=["Open www.evil.example/login now", "Fix DNS first: data:text/html,<script>x</script>",
                              "   ", "Run the pilot in retail."],
             views=["ImpactAssessment", "Script", "ResourceImpactTable", "ImpactAssessment", "Text"],
             suggested_actions=["run_assessment", "approve_package"]))
    out = ai(provider)
    assert out["mode"] == "ai" and out["requested_mode"] == "ai"
    assert out["mode_note"] == "Ignored view(s) outside the approved catalog."
    summary = out["summary"]
    assert summary["origin"] == "ai" and summary["model"] == "scripted-test-model"
    assert summary["label"].startswith("AI-generated summary")
    assert summary["text"] == "Three services would be denied. Details: [link removed] and [link removed]"
    recs = [f for f in out["findings"] if f["origin"] == "ai"]
    assert [f["kind"] for f in recs] == ["RECOMMENDATION"] * 3 and all(f["source"] is None for f in recs)
    assert [f["text"] for f in recs] == ["Open [link removed] now", "Fix DNS first: [link removed]",
                                         "Run the pilot in retail."]
    text = json.dumps(out)
    for bad in ("https://evil", "javascript:", "www.evil", "<script>", "\\u0007"):
        assert bad not in text, bad
    # Views: catalog components only, ControlSummary first and EvidencePanel last.
    assert [c["component"] for c in components(out) if c["component"] not in ("CanvasStack", "CanvasNotice")] == [
        "ControlSummary", "ImpactAssessment", "ResourceImpactTable", "EvidencePanel"]
    # Same authority as deterministic mode: values come from the persisted assessment, not the model.
    for key in ("control", "impact", "resources", "evidence"):
        assert views(out)[key] == views(det)[key], key
    assert views(out)["impact"]["run_id"] == run["id"]
    platform = [f for f in out["findings"] if f["origin"] == "platform"]
    assert platform and all(f in det["findings"] for f in platform)
    assert action(out, "run_assessment")["suggested_by_ai"] is True
    assert action(out, "prepare_rollout_draft")["suggested_by_ai"] is False
    assert out["steps"][0] == {"tool": "get_impact_assessment", "label": "AI assistant called get_impact_assessment",
                               "status": "ok", "detail": None}
    assert_safe_surface(out)


def test_model_choices_are_checked_against_the_catalogue_and_authorization(ai):
    out = ai(ScriptedProvider([], plan(scope_id="az-sub-payments-prod")), user="riley")
    assert out["mode"] == "ai"
    assert out["intent"]["entities"]["scope_id"] == "az-sub-retail-prod"
    assert out["mode_note"] == "Ignored a scope proposed by the model that is unknown or not readable."
    for marker in PAYMENTS_MARKERS:
        assert marker not in json.dumps(out).lower()

    out = ai(ScriptedProvider([], plan(control_id="CTL-DOES-NOT-EXIST")))
    assert out["intent"]["entities"]["control_id"] == AZ_CONTROL
    assert "Ignored a control proposed by the model that does not exist." in out["mode_note"]
    assert "CTL-DOES-NOT-EXIST" not in json.dumps(out)

    out = ai(ScriptedProvider([], plan(scope_id="az-sub-retail-prod")))
    ent = out["intent"]["entities"]
    assert ent["scope_id"] == "az-sub-retail-prod" and ent["scope_reason"] == "chosen by the AI assistant"
    assert out["context"]["scope"]["id"] == "az-sub-retail-prod"
    assert out["mode_note"] == "The AI assistant focused on scope az-sub-retail-prod."
    assert_safe_surface(out)


@pytest.mark.parametrize("final,note", [
    ("not json {", "did not match the required plan schema"),
    (None, "did not match the required plan schema"),
    (json.dumps(plan(html="<b>bold</b>")), "did not match the required plan schema"),
    (json.dumps({k: v for k, v in plan().items() if k != "summary"}), "did not match the required plan schema"),
    (json.dumps(plan(recommendations=["x"] * 6)), "did not match the required plan schema"),
    (json.dumps(plan(summary="")), "did not match the required plan schema"),
    (json.dumps([plan()]), "did not match the required plan schema"),
])
def test_invalid_plans_fall_back_to_deterministic(ai, final, note):
    out = ai(ScriptedProvider([("get_control_details", {"control_id": AZ_CONTROL})], final))
    assert out["mode"] == "deterministic" and out["requested_mode"] == "ai"
    assert note in out["mode_note"] and out["mode_note"].endswith("Showing the deterministic investigation.")
    assert out["summary"]["origin"] == "platform"
    assert all(f["origin"] == "platform" for f in out["findings"])
    assert not any("suggested_by_ai" in a for a in out["context"]["actions"])
    assert out["steps"][0]["label"] == "AI assistant called get_control_details"
    assert_safe_surface(out)
    event = ai_audit(ai.api)[0]
    assert event["details"]["outcome"] == "fallback" and event["details"]["tools_called"] == ["get_control_details"]


def test_provider_errors_fall_back_without_leaking_details(ai):
    out = ai(ScriptedProvider([], plan(), raise_error=RuntimeError("upstream said sk-ant-api03-SECRET")))
    assert out["mode"] == "deterministic"
    assert out["mode_note"] == ("AI-assisted mode failed: The AI provider failed (RuntimeError). Showing the "
                                "deterministic investigation.")
    assert "SECRET" not in json.dumps(out) and "SECRET" not in json.dumps(ai_audit(ai.api))
    validate_messages(out["a2ui"]["messages"])

    out = ai(FailingProvider([("get_control_details", {"control_id": AZ_CONTROL})], plan()))
    assert out["mode"] == "deterministic" and "The model declined this request." in out["mode_note"]
    assert_safe_surface(out)


def test_every_ai_run_is_audited(ai):
    ai(ScriptedProvider([("get_control_details", {"control_id": AZ_CONTROL}),
                         ("run_impact_assessment", {"implementation_revision_id": AZ_IMPL_REV,
                                                    "target_scope_id": "az-mg-prod"})], plan()))
    ai(ScriptedProvider([], "{"))
    ai(ScriptedProvider([], plan(), raise_error=TimeoutError()))
    deterministic(ai.api)
    events = ai_audit(ai.api)
    assert len(events) == 3
    by_outcome = [(e["details"]["outcome"], e["details"]["tools_called"]) for e in reversed(events)]
    assert by_outcome == [("ok", ["get_control_details", "run_impact_assessment"]), ("fallback", []),
                          ("fallback", [])]
    for e in events:
        assert e["actor_id"] == "u-cara" and e["object_type"] == "workspace_investigation"
        assert e["details"]["provider"] == "scripted" and e["details"]["model"] == "scripted-test-model"
        assert e["details"]["question"] == QUESTION
    assert events[0]["details"]["error"] == "The AI provider failed (TimeoutError)."
    assert events[2]["details"]["error"] is None


def test_ai_investigation_audit_is_not_visible_outside_the_investigated_scope(ai):
    ai(ScriptedProvider([], plan()))
    assert len(ai_audit(ai.api, "vic")) == 1
    assert ai_audit(ai.api, "riley") == []
    ai(ScriptedProvider([], plan(scope_id="az-sub-retail-prod")), user="riley")
    assert len(ai_audit(ai.api, "riley")) == 1
    assert len(ai_audit(ai.api, "vic")) == 2


# ------------------------------------------------------------------------------------ configuration


def test_ai_mode_unavailable_without_provider_falls_back(ai, app, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert app.state.settings.workspace_ai_provider == "none"
    out = ai(None)
    assert out["mode"] == "deterministic" and out["requested_mode"] == "ai"
    assert out["mode_note"] == ("AI-assisted mode is unavailable: AI-assisted mode is not configured "
                                "(WORKSPACE_AI_PROVIDER=none). Deterministic mode is fully functional. Showing the "
                                "deterministic investigation.")
    assert_safe_surface(out)
    assert ai_audit(ai.api) == []  # nothing was sent anywhere
    app.state.settings.workspace_ai_provider = "anthropic"
    out = ai(None)
    assert "No Anthropic credentials are configured (ANTHROPIC_API_KEY). Showing" in out["mode_note"]
    feats = ai.api.get("cara", "/features", expected=200).json()["workspace_ai"]
    assert feats["available"] is False and "ANTHROPIC_API_KEY" in feats["reason"]


def test_ai_status(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    def status(**kw):
        base = {"enable_a2ui_workspace": True, "workspace_ai_provider": "anthropic", "workspace_ai_effort": "medium"}
        return ai_status(Settings(**{**base, **kw}))

    assert status(enable_a2ui_workspace=False)["reason"] == "The AI Control Workspace is disabled."
    assert "WORKSPACE_AI_PROVIDER=none" in status(workspace_ai_provider="none")["reason"]
    assert "not an approved provider" in status(workspace_ai_provider="openai")["reason"]
    assert "WORKSPACE_AI_EFFORT must be one of" in status(workspace_ai_effort="extreme")["reason"]
    missing = status()
    assert missing["available"] is False and "ANTHROPIC_API_KEY" in missing["reason"]
    for kw in ({"enable_a2ui_workspace": False}, {"workspace_ai_provider": "none"},
               {"workspace_ai_provider": "openai"}, {"workspace_ai_effort": "extreme"}, {}):
        assert status(**kw)["available"] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
    ok = status()
    assert ok == {"provider": "anthropic", "model": "claude-opus-5-5", "refusal_fallbacks": True,
                  "available": True, "reason": None}
    assert "sk-ant" not in json.dumps(ok)
    assert status(workspace_ai_effort="bogus")["available"] is False
    override = ai_status(Settings(), ScriptedProvider([], None))
    assert override["available"] is True and override["provider"] == "scripted"


def test_clean_text():
    assert clean_text("see https://x.example/a?b=c, ftp://f and FILE:///etc/passwd", 200) == \
        "see [link removed] [link removed] and [link removed]"
    assert clean_text("JavaScript:alert(1) data:image/png;base64,AAA www.example.com", 200) == \
        "[link removed] [link removed] [link removed]"
    assert clean_text("\x00bell\x07\x1b[31mred", 200) == "bell[31mred"
    assert clean_text("  " + "a" * 50, 10) == "a" * 10
    assert clean_text("line one\nline two\ttab", 200) == "line one\nline two\ttab"


# ------------------------------------------------------------------------------------ Anthropic provider


class FakeClient:
    """Stands in for ``anthropic.Anthropic``: records request kwargs and replays canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.options: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, **options):
        self.options.append(options)
        return self

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def tool_use(*blocks, rid: str = "req_1"):
    content = [SimpleNamespace(type="thinking", thinking="", signature="sig-1")]
    content += [SimpleNamespace(type="tool_use", id=bid, name=name, input=inp) for bid, name, inp in blocks]
    return SimpleNamespace(stop_reason="tool_use", content=content, model="claude-opus-5-5", _request_id=rid)


def final(text: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)], model=model,
                           _request_id="req_final")


@pytest.fixture
def provider_factory(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy")

    def make(responses, **kw):
        args = {"model": "claude-opus-5-5", "effort": "medium", "timeout_seconds": 5.0, "refusal_fallbacks": True,
                **kw}
        provider = AnthropicProvider(**args)
        provider.client = FakeClient(responses)
        return provider
    return make


def run_provider(provider, max_tool_calls: int = 8):
    seen: list[tuple[str, dict]] = []

    def call_tool(name, raw):
        seen.append((name, raw))
        return json.dumps({"tool": name, "ok": True}), False

    tools = tool_specs({"READ"})
    outcome = provider.investigate(system="SYSTEM", user="USER", tools=tools, call_tool=call_tool,
                                   plan_schema=PLAN_SCHEMA, max_tool_calls=max_tool_calls)
    return outcome, seen, tools


def test_anthropic_provider_request_shape_and_tool_loop(provider_factory):
    text = json.dumps(plan())
    provider = provider_factory([tool_use(("toolu_1", "get_control_details", {"control_id": AZ_CONTROL})),
                                 final(text)])
    outcome, seen, tools = run_provider(provider)
    assert outcome.error is None and outcome.final_text == text
    assert outcome.tool_calls == [{"tool": "get_control_details", "ok": True}]
    assert outcome.request_ids == ["req_1", "req_final"] and outcome.served_model == "claude-opus-5-5"
    assert seen == [("get_control_details", {"control_id": AZ_CONTROL})]
    first, second = provider.client.calls
    assert first["model"] == "claude-opus-5-5" and first["max_tokens"] == 16000 and first["system"] == "SYSTEM"
    assert [t["name"] for t in first["tools"]] == [t["name"] for t in tools]
    assert all(t["strict"] is True for t in first["tools"])
    assert first["tool_choice"] == {"type": "auto"}
    assert first["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": PLAN_SCHEMA}}
    assert first["betas"] == [FALLBACK_BETA] == ["server-side-fallback-2026-07-01"]
    assert first["fallbacks"] == "default"
    for forbidden in ("thinking", "budget_tokens", "temperature", "top_p", "top_k"):
        assert forbidden not in first
    assert first["messages"] == [{"role": "user", "content": "USER"}]
    # The assistant turn goes back unchanged and the tool result is bound to its tool_use id.
    assert len(second["messages"]) == 3
    assistant, results = second["messages"][1], second["messages"][2]
    assert assistant["role"] == "assistant" and assistant["content"][0].type == "thinking"
    assert results == {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_1",
        "content": json.dumps({"tool": "get_control_details", "ok": True}), "is_error": False}]}


def test_anthropic_provider_without_refusal_fallbacks(provider_factory):
    provider = provider_factory([final("{}")], refusal_fallbacks=False, effort="high")
    run_provider(provider)
    call = provider.client.calls[0]
    assert "betas" not in call and "fallbacks" not in call
    assert call["output_config"]["effort"] == "high"


@pytest.mark.parametrize("stop_reason,error", [
    ("refusal", "The model declined this request."),
    ("max_tokens", "The model response was cut off (max_tokens)."),
])
def test_anthropic_provider_stop_reasons(provider_factory, stop_reason, error):
    provider = provider_factory([final("partial", stop_reason=stop_reason)])
    outcome, seen, _ = run_provider(provider)
    assert outcome.error == error and outcome.final_text is None and outcome.stop_reason == stop_reason
    assert seen == []


def test_anthropic_provider_enforces_tool_budget_and_turn_limit(provider_factory):
    provider = provider_factory([
        tool_use(("t1", "get_control_details", {"control_id": AZ_CONTROL}),
                 ("t2", "get_implementations", {"control_id": AZ_CONTROL}),
                 ("t3", "get_rollout_status", {"control_id": AZ_CONTROL})),
        tool_use(("t4", "get_exceptions", {"control_id": AZ_CONTROL, "scope_id": None})),
        final("{}")])
    outcome, seen, _ = run_provider(provider, max_tool_calls=2)
    assert [name for name, _ in seen] == ["get_control_details", "get_implementations"]
    assert [c["ok"] for c in outcome.tool_calls] == [True, True, False, False]
    results = provider.client.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2", "t3"]
    assert results[2]["is_error"] is True and results[2]["content"].startswith("Tool budget exhausted")
    assert outcome.final_text == "{}"

    looping = provider_factory([tool_use(("t", "get_control_details", {"control_id": AZ_CONTROL}))])
    outcome, seen, _ = run_provider(looping, max_tool_calls=100)
    assert outcome.error == "The model did not finish within the turn limit." and outcome.final_text is None
    assert len(looping.client.calls) == MAX_TURNS and len(seen) == MAX_TURNS


def test_anthropic_provider_pause_turn_and_non_dict_input(provider_factory):
    paused = SimpleNamespace(stop_reason="pause_turn", content=[SimpleNamespace(type="text", text="...")],
                             model="claude-opus-5-5")
    provider = provider_factory([paused, tool_use(("t1", "get_control_details", "not-a-dict")), final("{}")])
    outcome, seen, _ = run_provider(provider)
    assert seen == [("get_control_details", {})]
    assert provider.client.calls[1]["messages"][-1] == {"role": "assistant", "content": paused.content}
    assert outcome.final_text == "{}"


def test_anthropic_provider_api_errors_become_short_reasons(provider_factory):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(cls, code):
        return cls("boom sk-ant-SECRET", response=httpx2.Response(code, request=req), body=None)

    cases = [
        (anthropic.APIConnectionError(request=req), "could not be reached"),
        (status(anthropic.AuthenticationError, 401), "rejected the credentials"),
        (status(anthropic.RateLimitError, 429), "rate limit"),
        (status(anthropic.BadRequestError, 400), "bad request"),
        (status(anthropic.NotFoundError, 404), "does not recognise model 'claude-opus-5-5'"),
        (status(anthropic.InternalServerError, 529), "HTTP 529"),
    ]
    for exc, reason in cases:
        outcome, _, _ = run_provider(provider_factory([exc]))
        assert reason in outcome.error and "SECRET" not in outcome.error


def test_ai_mode_end_to_end_with_fake_anthropic_client(ai, app, monkeypatch):
    """Configured provider path (no override): the SDK client is faked, everything else is real."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
    app.state.settings.workspace_ai_provider = "anthropic"
    fake = FakeClient([tool_use(("t1", "get_impact_assessment", {"control_id": AZ_CONTROL, "scope_id": "az-mg-prod"}),
                                ("t2", "run_impact_assessment", {"implementation_revision_id": AZ_IMPL_REV,
                                                                 "target_scope_id": "az-mg-prod"})),
                       final(json.dumps(plan()), model="claude-opus-5")])
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: fake)
    feats = ai.api.get("cara", "/features", expected=200).json()["workspace_ai"]
    assert feats["available"] is True and feats["provider"] == "anthropic" and "sk-ant" not in json.dumps(feats)
    out = ai(None)
    assert out["mode"] == "ai" and out["summary"]["model"] == "claude-opus-5"
    results = fake.calls[1]["messages"][-1]["content"]
    assert results[0]["is_error"] is False and json.loads(results[0]["content"])["available"] is False
    assert results[1]["is_error"] is True and "not available" in results[1]["content"]
    assert counts_for(ai.api)["assessments"] == 0
    event = ai_audit(ai.api)[0]["details"]
    assert event["provider"] == "anthropic" and event["model"] == "claude-opus-5" and event["outcome"] == "ok"
    assert event["tools_called"] == ["get_impact_assessment", "run_impact_assessment"]
    assert "sk-ant" not in json.dumps(out)
    assert_safe_surface(out)
