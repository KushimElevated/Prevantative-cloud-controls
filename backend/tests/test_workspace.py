"""Optional AI Control Workspace (deterministic mode): flag, preferences, investigations, authorization, drafts.

Driven only through the public API. Every number on the canvas must come from the existing services,
so impact counts are recomputed here independently from the Classic assessment results endpoint.
"""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from app.workspace.a2ui import validate_messages
from app.workspace.catalog import SAFE_HREF, catalog_document
from journey import AZ_CONTROL, AZ_IMPL_REV, assess, submit_control_and_impl

QUESTION = "What would happen if we prevented public network access for all Azure AI Search services in production?"
RETAIL_PARTNER = ("/subscriptions/00000000-0000-0000-0000-000000000101/resourceGroups/rg-retail-search/providers/"
                  "Microsoft.Search/searchServices/srch-retail-partner")
WORKSPACE_ENDPOINTS = [
    ("GET", "/workspace/catalog", None),
    ("GET", "/workspace/tools", None),
    ("GET", "/workspace/intents", None),
    ("POST", "/workspace/investigations", {"question": QUESTION}),
    ("POST", "/workspace/drafts/prepare", {"kind": "exception", "params": {"control_id": AZ_CONTROL,
                                                                          "resource_id": RETAIL_PARTNER}}),
    ("POST", "/workspace/drafts/submit", {"kind": "exception", "basis": {}, "payload": {}, "confirmed": True}),
]


@pytest.fixture
def ws(app, api):
    app.state.settings.enable_a2ui_workspace = True
    return api


def investigate(api, user: str, question: str = QUESTION, expected: int = 200, **extra) -> dict:
    return api.post(user, "/workspace/investigations", {"question": question, **extra}, expected=expected).json()


def views(result: dict) -> dict:
    msgs = result["a2ui"]["messages"]
    return next(m["updateDataModel"]["value"]["views"] for m in msgs if "updateDataModel" in m)


def components(result: dict) -> list[dict]:
    return next(m["updateComponents"]["components"] for m in result["a2ui"]["messages"] if "updateComponents" in m)


def action(result: dict, action_id: str) -> dict:
    return next(a for a in result["context"]["actions"] if a["id"] == action_id)


def hrefs(node) -> list[str]:
    if isinstance(node, dict):
        return [v for k, v in node.items() if k == "href" and isinstance(v, str)] + \
            [h for v in node.values() for h in hrefs(v)]
    if isinstance(node, list):
        return [h for v in node for h in hrefs(v)]
    return []


def assert_safe_surface(result: dict) -> None:
    a2ui = result["a2ui"]
    assert a2ui["protocol_version"] == "v0.9" and a2ui["catalog_id"] == "urn:ccp:a2ui:control-workspace:v1"
    validate_messages(a2ui["messages"])
    assert all(next(iter(m["createSurface"].values())) == a2ui["surface_id"]
               for m in a2ui["messages"] if "createSurface" in m)
    found = hrefs(result)
    assert found, "expected links in the investigation"
    bad = [h for h in found if not re.match(SAFE_HREF, h)]
    assert not bad, bad


def scope_paths(api, user: str = "cara") -> dict[str, list[str]]:
    return {s["id"]: [p["id"] for p in s["path"]] for s in api.get(user, "/scopes", expected=200).json()["items"]}


def results(api, run_id: str, kind: str, user: str = "cara") -> list[dict]:
    body = api.get(user, f"/assessments/{run_id}/results?subject_kind={kind}&limit=200", expected=200).json()
    assert body["total"] <= 200
    return body["items"]


def audit(api, action_name: str, **filters) -> list[dict]:
    q = "&".join(f"{k}={v}" for k, v in {"action": action_name, "limit": 50, **filters}.items())
    return api.get("cara", f"/audit?{q}", expected=200).json()["items"]


def counts_for(api, user: str = "cara") -> dict[str, int]:
    return {"exceptions": api.get(user, "/exceptions?limit=200", expected=200).json()["total"],
            "plans": api.get(user, "/rollout-plans?limit=200", expected=200).json()["total"],
            "assessments": api.get(user, "/assessments?limit=200", expected=200).json()["total"]}


# ------------------------------------------------------------------------------------ flag and preferences


def test_workspace_disabled_by_default_and_classic_unaffected(api, client):
    assert api.get("cara", "/features", expected=200).json() == {
        "a2ui_workspace": {"enabled": False},
        "workspace_ai": {"available": False, "reason": "The AI Control Workspace is disabled."}}
    for method, path, body in WORKSPACE_ENDPOINTS:
        r = api.call("cara", method, path, json=body, expected=404)
        assert r.json()["error"]["code"] == "NOT_FOUND"
    # Even malformed requests do not reveal the endpoints.
    api.post("cara", "/workspace/investigations", {"bogus": 1}, expected=404)
    assert client.get("/api/v1/workspace/catalog").status_code in (401, 404)
    assert api.get("cara", "/me/preferences", expected=200).json() == {
        "experience": "classic", "effective_experience": "classic", "workspace_mode": "deterministic"}
    r = api.put("cara", "/me/preferences", {"experience": "workspace"}, expected=422).json()
    assert r["error"]["code"] == "VALIDATION_FAILED"
    assert api.get("cara", "/me/preferences", expected=200).json()["experience"] == "classic"
    api.put("cara", "/me/preferences", {"experience": "classic"}, expected=200)
    # Classic endpoints behave exactly as before.
    assert api.get("cara", "/controls", expected=200).json()["total"] >= 2
    api.get("cara", f"/controls/{AZ_CONTROL}", expected=200)
    api.get("riley", "/exceptions/exc-az-payments-legacy", expected=404)
    run = assess(api)
    assert run["status"] == "COMPLETED"


def test_features_and_preferences(ws, app):
    feats = ws.get("cara", "/features", expected=200).json()
    assert feats["a2ui_workspace"] == {"enabled": True}
    ai = feats["workspace_ai"]
    assert ai["available"] is False and "WORKSPACE_AI_PROVIDER=none" in ai["reason"]
    assert ai["provider"] == "none" and ai["model"] == "claude-opus-5-5"
    assert "ANTHROPIC" not in json.dumps(feats).replace("ANTHROPIC_API_KEY", "")

    default = {"experience": "classic", "effective_experience": "classic", "workspace_mode": "deterministic"}
    assert ws.get("cara", "/me/preferences", expected=200).json() == default
    out = ws.put("cara", "/me/preferences", {"experience": "workspace", "workspace_mode": "ai"}, expected=200).json()
    assert out == {"experience": "workspace", "effective_experience": "workspace", "workspace_mode": "ai"}
    assert ws.get("cara", "/me/preferences", expected=200).json() == out
    # Preferences are per user.
    assert ws.get("eli", "/me/preferences", expected=200).json() == default

    events = audit(ws, "user.preferences_updated", object_id="u-cara")
    assert len(events) == 1
    assert events[0]["actor_id"] == "u-cara" and events[0]["object_type"] == "user"
    assert events[0]["details"]["before"] == {}
    assert events[0]["details"]["after"] == {"experience": "workspace", "workspace_mode": "ai"}

    for bad in ({"experience": "workspace", "theme": "dark"}, {"experience": "modern"},
                {"experience": "workspace", "workspace_mode": "autonomous"}, {"workspace_mode": "ai"},
                {"experience": "workspace", "roles": ["ADMIN"]}):
        r = ws.put("cara", "/me/preferences", bad, expected=422).json()
        assert r["error"]["code"] == "REQUEST_INVALID", bad
    assert ws.get("cara", "/me/preferences", expected=200).json() == out

    # Turning the flag off keeps the stored choice but the effective experience is Classic.
    app.state.settings.enable_a2ui_workspace = False
    assert ws.get("cara", "/me/preferences", expected=200).json() == {
        "experience": "workspace", "effective_experience": "classic", "workspace_mode": "ai"}
    assert ws.get("cara", "/features", expected=200).json()["a2ui_workspace"] == {"enabled": False}
    ws.get("cara", "/workspace/intents", expected=404)


def test_catalog_tools_and_intents_endpoints(ws):
    assert ws.get("vic", "/workspace/catalog", expected=200).json() == json.loads(json.dumps(catalog_document()))
    tools = {t["api_name"]: t for t in ws.get("vic", "/workspace/tools", expected=200).json()["items"]}
    assert tools["run_impact_assessment"]["kind"] == "COMMAND"
    assert "POST /api/v1/assessments" in tools["run_impact_assessment"]["executed_by"]
    assert {t["kind"] for n, t in tools.items() if n.startswith("prepare_")} == {"DRAFT"}
    assert tools["get_impact_assessment"]["kind"] == "READ"
    intents = ws.get("vic", "/workspace/intents", expected=200).json()
    assert {i["id"] for i in intents["intents"]} >= {"impact_preview", "exception_review", "rollout_status",
                                                     "readiness", "coverage_status", "audit_history",
                                                     "control_overview"}
    assert QUESTION in intents["examples"]
    ws.get("riley", "/workspace/tools", expected=200)
    assert ws.client.get("/api/v1/workspace/tools").status_code == 401


# ------------------------------------------------------------------------------------ deterministic investigation


def test_impact_investigation_without_and_with_assessment(ws):
    before = investigate(ws, "cara")
    assert before["mode"] == "deterministic" and before["mode_note"] is None
    assert before["requested_mode"] == "deterministic"
    ent = before["intent"]["entities"]
    assert before["intent"]["id"] == "impact_preview"
    assert ent["control_id"] == AZ_CONTROL and ent["scope_id"] == "az-mg-prod"
    assert ent["provider"] == "azure" and ent["resource_type"] == "Microsoft.Search/searchServices"
    assert before["context"]["scope"]["id"] == "az-mg-prod"

    impact = views(before)["impact"]
    assert impact["available"] is False and impact.get("configuration") is None and not impact.get("run_id")
    run_action = action(before, "run_assessment")
    assert run_action["enabled"] is True and run_action["reason"] is None
    assert run_action["params"] == {"implementation_revision_id": AZ_IMPL_REV, "target_scope_id": "az-mg-prod"}
    assert run_action["kind"] == "command" and run_action["confirm"]
    assert impact["run_action"] == run_action["params"]
    # No fabricated counts: only control/implementation evidence, impact is explicitly UNKNOWN.
    count_text = re.compile(r"\b\d+\s+(compliant|non-compliant|resource|request|ready|blocked)", re.IGNORECASE)
    for f in before["findings"]:
        assert f["origin"] == "platform"
        if f["kind"] == "EVIDENCE":
            assert not count_text.search(f["text"]), f
            assert (f["source"] or {}).get("type") != "assessment_run"
    assert any(f["kind"] == "UNKNOWN" and "UNKNOWN" in f["text"] for f in before["findings"])
    assert "impact is unknown" in before["summary"]["text"] and before["summary"]["origin"] == "platform"
    notices = [c for c in components(before) if c["component"] == "CanvasNotice"]
    assert any(n["tone"] == "warning" and "UNKNOWN" in n["text"] for n in notices)
    assert before["context"]["basis"]["assessment_run_id"] is None
    assert_safe_surface(before)

    # Run an assessment through the existing Classic endpoint, then ask again.
    run = assess(ws, "az-mg-contoso")
    after = investigate(ws, "cara")
    impact = views(after)["impact"]
    assert impact["available"] is True and impact["run_id"] == run["id"]
    assert impact["target_scope_id"] == "az-mg-contoso" and impact["requested_scope_id"] == "az-mg-prod"
    assert impact["filtered_to_scope"] is True and "No re-evaluation was performed" in impact["filter_note"]
    assert after["context"]["basis"]["assessment_run_id"] == run["id"]

    # Independent recomputation from GET /assessments/{id}/results restricted to az-mg-prod.
    paths = scope_paths(ws)

    def in_prod(r: dict) -> bool:
        return r["scope_id"] is not None and "az-mg-prod" in paths[r["scope_id"]]

    all_res = results(ws, run["id"], "RESOURCE")
    res = [r for r in all_res if in_prod(r)]
    assert 0 < len(res) < len(all_res), "the scope filter must matter for this check"
    applicable = [r for r in res if r["applicability"] == "APPLICABLE"]
    assert impact["total_resources"] == len(res)
    assert {k: v for k, v in impact["configuration"].items() if v} == dict(Counter(r["configuration_result"]
                                                                                   for r in res))
    assert sum(impact["configuration"].values()) == len(res) and impact["reconciles"] is True
    assert {k: v for k, v in impact["exceptions"].items() if v} == dict(Counter(r["exception_disposition"]
                                                                                for r in applicable))
    assert {k: v for k, v in impact["readiness"].items() if v} == dict(Counter(r["readiness"] or "NOT_EVALUATED"
                                                                               for r in res))
    reqs = [r for r in results(ws, run["id"], "REQUEST") if in_prod(r)]
    req = impact["request_impact"]
    assert req["evidence_present"] is True and req["total"] == len(reqs)
    assert {k: v for k, v in req["counts"].items() if v} == dict(Counter(r["request_impact"] for r in reqs))
    # The resource table lists exactly those resources.
    table = views(after)["resources"]
    assert table["run_id"] == run["id"] and table["total"] == len(res) and not table["truncated"]
    assert sorted(r["resource_id"] for r in table["rows"]) == sorted(r["subject_id"] for r in res)
    # The rollup for the whole run (not filtered) is what the Classic endpoint returns.
    whole = investigate(ws, "cara", scope_id="az-mg-contoso")
    wimp = views(whole)["impact"]
    assert wimp["filtered_to_scope"] is False
    assert wimp["configuration"] == ws.get("cara", f"/assessments/{run['id']}", expected=200).json()[
        "rollup"]["configuration"]["counts"]

    kinds = {f["kind"] for f in after["findings"]}
    assert {"EVIDENCE", "ESTIMATE", "UNKNOWN"} <= kinds
    evidence = next(f for f in after["findings"] if f["text"].startswith(f"Assessment {run['id']} evaluated"))
    assert f"evaluated {len(res)} resource(s) in az-mg-prod" in evidence["text"]
    assert evidence["source"] == {"type": "assessment_run", "id": run["id"], "href": f"/assessments/{run['id']}"}
    assert all(f["origin"] == "platform" for f in after["findings"])
    assert str(impact["configuration"]["NON_COMPLIANT"]) in after["summary"]["text"]
    assert_safe_surface(after)
    assert_safe_surface(whole)
    # Investigations are read-only.
    assert counts_for(ws)["assessments"] == 1


@pytest.mark.parametrize("question,intent", [
    ("What exceptions exist for Azure AI Search public network access?", "exception_review"),
    ("Is the AWS S3 public access control ready to roll out?", "rollout_status"),
    ("Which applications are blocked by missing private endpoint evidence?", "readiness"),
    ("Which Azure AI Search services in retail production are not compliant?", "coverage_status"),
    ("Who approved changes to CTL-AZ-SEARCH-PNA?", "audit_history"),
    ("Tell me about key vault firmware", "control_overview"),
])
def test_example_questions_produce_valid_surfaces(ws, question, intent):
    assess(ws)
    for user in ("cara", "riley", "vic"):
        out = investigate(ws, user, question)
        assert out["intent"]["id"] == intent
        validate_messages(out["a2ui"]["messages"])
        assert all(re.match(SAFE_HREF, h) for h in hrefs(out))


@pytest.mark.parametrize("question,scope_id", [
    (QUESTION, "aws-root"),
    ("Is the AWS S3 public access control ready to roll out?", "az-mg-prod"),
    ("What would happen if we enforced CTL-AWS-S3-PUBLIC-ACCESS in az-rg-retail-search?", None),
])
def test_scope_from_another_provider_is_handled(ws, question, scope_id):
    assess(ws)
    out = investigate(ws, "cara", question, **({"scope_id": scope_id} if scope_id else {}))
    impact = views(out)["impact"]
    assert impact["available"] is False and impact["can_run"] is False
    assert impact["run_blocked_reason"].startswith("No ")
    assert action(out, "run_assessment")["enabled"] is False
    assert_safe_surface(out)


def test_unmatched_question_offers_control_proposal(ws):
    for user, enabled in (("cara", True), ("vic", False)):
        out = investigate(ws, user, "Zebra giraffe xylophone quokka?")
        assert out["intent"]["id"] == "control_overview" and out["intent"]["entities"]["control_id"] is None
        assert [a["id"] for a in out["context"]["actions"]] == ["prepare_control_draft"]
        assert action(out, "prepare_control_draft")["enabled"] is enabled
        assert [f["kind"] for f in out["findings"]] == ["UNKNOWN"]
        assert out["summary"]["text"] == "No catalogued control matches this question."
        assert [c["component"] for c in components(out)] == ["CanvasStack", "CanvasNotice"]
        assert_safe_surface(out)
    bad = investigate(ws, "cara", "x", expected=422)
    assert bad["error"]["code"] == "REQUEST_INVALID"
    investigate(ws, "cara", QUESTION, expected=422, mode="autonomous")
    investigate(ws, "cara", QUESTION, expected=422, scope_id="../etc/passwd")
    investigate(ws, "cara", QUESTION, expected=422, tools=["run_impact_assessment"])


# ------------------------------------------------------------------------------------ authorization


def test_scoped_user_sees_only_authorised_scopes(ws):
    assess(ws)
    out = investigate(ws, "riley")
    assert out["intent"]["entities"]["scope_id"] == "az-sub-retail-prod"
    text = json.dumps(out).lower()
    for leak in ("payments", "az-sub-payments-prod", "srch-payments", "az-rg-payments-search", "000000000102"):
        assert leak not in text, leak
    v = views(out)
    assert v["impact"]["available"] is True and v["impact"]["filtered_to_scope"] is True
    assert v["impact"]["confidence"] is None and v["impact"]["disclosure"] is None
    assert {o["scope_id"] for o in v["scope"]["options"]} == {"az-sub-retail-prod", "az-rg-retail-search"}
    assert v["resources"]["rows"] and all(r["scope_id"] in ("az-sub-retail-prod", "az-rg-retail-search")
                                          for r in v["resources"]["rows"])
    run_action = action(out, "run_assessment")
    assert run_action["enabled"] is False and "CONTROL_ENGINEER" in run_action["reason"]
    assert run_action["params"] is None
    assert_safe_surface(out)

    # Asking for an unreadable scope (or one that does not exist) gives the same answer and leaks nothing.
    forced = investigate(ws, "riley", scope_id="az-sub-payments-prod")
    missing = investigate(ws, "riley", scope_id="az-sub-doesnotexist")
    assert forced["intent"]["entities"]["scope_id"] == "az-sub-retail-prod"
    assert forced["intent"]["notes"] == ["Scope az-sub-payments-prod is not available to your identity."]
    assert missing["intent"]["notes"] == ["Scope az-sub-doesnotexist is not available to your identity."]
    forced_text = json.dumps({k: v for k, v in forced.items() if k not in ("intent",)}).lower()
    assert "payments" not in forced_text
    assert views(forced)["impact"] == views(out)["impact"]
    named = investigate(ws, "riley", QUESTION + " Focus on az-sub-payments-prod.")
    unknown = investigate(ws, "riley", QUESTION + " Focus on az-sub-doesnotexist.")
    assert named["intent"]["entities"]["scope_id"] == "az-sub-retail-prod"
    assert named["intent"]["notes"] == unknown["intent"]["notes"]
    assert "payments" not in json.dumps({k: v for k, v in named.items() if k != "question"}).lower()
    # A readable scope named in the question is honoured.
    retail = investigate(ws, "riley", QUESTION + " Focus on az-rg-retail-search.")
    assert retail["intent"]["entities"]["scope_id"] == "az-rg-retail-search"
    assert retail["intent"]["entities"]["scope_reason"] == "named in the question"
    # The Classic API agrees.
    ws.get("riley", "/scopes/az-sub-payments-prod", expected=404)


def test_viewer_cannot_run_or_draft(ws):
    out = investigate(ws, "vic")
    assert out["intent"]["entities"]["scope_id"] == "az-mg-prod"
    run_action = action(out, "run_assessment")
    assert run_action["enabled"] is False
    assert run_action["reason"] == "Running an assessment requires the CONTROL_ENGINEER role."
    rollout = action(out, "prepare_rollout_draft")
    assert rollout["enabled"] is False and rollout["reason"] == "Requires CONTROL_ENGINEER."
    assert any(f["kind"] == "RECOMMENDATION" and "Ask a control engineer" in f["text"] for f in out["findings"])
    # The command itself stays with the existing endpoint, which refuses the viewer.
    ws.post("vic", "/assessments", {"implementation_revision_id": AZ_IMPL_REV, "target_scope_id": "az-mg-prod"},
            expected=403)


# ------------------------------------------------------------------------------------ drafts


def _retail_partner_id(ws) -> str:
    rows = views(investigate(ws, "riley"))["resources"]["rows"]
    row = next(r for r in rows if r["name"] == "srch-retail-partner")
    assert row["can_prepare_exception"] is True
    return row["resource_id"]


def _filled(payload: dict) -> dict:
    return {**payload, "business_justification": "Partner API needs public ingress until the private link ships.",
            "technical_justification": "Partner integration cannot route through the private endpoint yet.",
            "risk_owner": "Retail Digital CISO delegate", "compensating_controls": ["IP allow-list", "WAF"]}


def test_exception_draft_submitted_through_existing_service(ws):
    assess(ws)
    resource_id = _retail_partner_id(ws)
    assert resource_id == RETAIL_PARTNER
    draft = ws.post("riley", "/workspace/drafts/prepare",
                    {"kind": "exception", "params": {"control_id": AZ_CONTROL, "resource_id": resource_id}},
                    expected=200).json()
    assert draft["kind"] == "exception" and draft["can_submit"] is True and draft["blocking_reasons"] == []
    assert draft["required_fields"] == ["business_justification", "technical_justification", "risk_owner",
                                        "compensating_controls"]
    assert set(draft["basis"]) == {"control_revision_id", "control_revision_digest", "implementation_revision_id",
                                   "implementation_revision_digest", "resource_snapshot_id"}
    assert draft["basis"]["implementation_revision_id"] == AZ_IMPL_REV
    p = draft["payload"]
    assert p["application"] == "retail-partner-api" and p["scope_id"] == "az-rg-retail-search"
    assert p["resource_ids"] == [resource_id] and p["business_justification"] == ""
    assert draft["preview"]["representability"]
    before = counts_for(ws, "riley")["exceptions"]

    body = {"kind": "exception", "basis": draft["basis"], "payload": _filled(p)}
    for confirmed in ({}, {"confirmed": False}, {"confirmed": "yes"}):
        r = ws.post("riley", "/workspace/drafts/submit", {**body, **confirmed}, expected=422).json()
        assert r["error"]["code"] == "REQUEST_INVALID"
    ws.post("riley", "/workspace/drafts/submit", {**body, "confirmed": True, "auto_approve": True}, expected=422)
    # The existing ExceptionCreate validation applies: a person must write the justification.
    r = ws.post("riley", "/workspace/drafts/submit", {**body, "payload": p, "confirmed": True}, expected=422).json()
    assert r["error"]["code"] == "VALIDATION_FAILED"
    assert {tuple(d["loc"]) for d in r["error"]["details"]} >= {("business_justification",),
                                                                ("technical_justification",)}
    assert counts_for(ws, "riley")["exceptions"] == before

    out = ws.post("riley", "/workspace/drafts/submit", {**body, "confirmed": True}, expected=201).json()
    created = out["created"]
    assert created["type"] == "exception" and created["href"] == f"/exceptions/{created['id']}"
    assert re.match(SAFE_HREF, created["href"])
    exc = ws.get("riley", f"/exceptions/{created['id']}", expected=200).json()
    assert exc["governance_status"] == "REQUESTED" and exc["effective_status"] == "REQUESTED"
    assert exc["requester_id"] == "u-riley" and exc["resource_ids"] == [resource_id]
    assert exc["business_justification"] == _filled(p)["business_justification"]
    assert counts_for(ws, "riley")["exceptions"] == before + 1
    submitted = audit(ws, "workspace.draft_submitted", object_id=created["id"])
    assert len(submitted) == 1 and submitted[0]["actor_id"] == "u-riley"
    assert submitted[0]["details"]["source"] == "ai_control_workspace" and submitted[0]["details"]["confirmed"]
    assert submitted[0]["details"]["basis"] == draft["basis"]
    assert len(audit(ws, "exception.requested", object_id=created["id"])) == 1

    # A tampered basis is refused as stale.
    stale = {**body, "basis": {**draft["basis"], "resource_snapshot_id": "snap-old"}, "confirmed": True}
    r = ws.post("riley", "/workspace/drafts/submit", stale, expected=409).json()
    assert r["error"]["code"] == "DRAFT_STALE" and r["error"]["details"]["changed"] == ["resource_snapshot_id"]


def test_exception_draft_refused_for_other_scopes_and_bad_params(ws):
    payments = ("/subscriptions/00000000-0000-0000-0000-000000000102/resourceGroups/rg-payments-search/providers/"
                "Microsoft.Search/searchServices/srch-payments-kb")
    r = ws.post("riley", "/workspace/drafts/prepare",
                {"kind": "exception", "params": {"control_id": AZ_CONTROL, "resource_id": payments}},
                expected=404).json()
    assert "payments" not in json.dumps(r).replace(payments, "").lower()
    r = ws.post("riley", "/workspace/drafts/prepare",
                {"kind": "exception", "params": {"control_id": AZ_CONTROL, "resource_id": RETAIL_PARTNER,
                                                 "scope_id": "az-sub-payments-prod"}}, expected=422).json()
    assert r["error"]["code"] == "VALIDATION_FAILED" and r["error"]["details"][0]["loc"] == ["scope_id"]
    ws.post("riley", "/workspace/drafts/prepare",
            {"kind": "exception", "params": {"control_id": "../../etc", "resource_id": RETAIL_PARTNER}},
            expected=422)
    ws.post("riley", "/workspace/drafts/prepare", {"kind": "approval", "params": {}}, expected=422)
    # A viewer may look at a draft but the server says why it cannot be submitted.
    draft = ws.post("vic", "/workspace/drafts/prepare",
                    {"kind": "exception", "params": {"control_id": AZ_CONTROL, "resource_id": RETAIL_PARTNER}},
                    expected=200).json()
    assert draft["can_submit"] is False and "EXCEPTION_REQUESTER" in draft["blocking_reasons"][0]
    ws.post("vic", "/workspace/drafts/submit", {"kind": "exception", "basis": draft["basis"],
                                                "payload": _filled(draft["payload"]), "confirmed": True},
            expected=403)


def test_exception_draft_stale_after_new_implementation_revision(ws):
    submit_control_and_impl(ws)
    draft = ws.post("riley", "/workspace/drafts/prepare",
                    {"kind": "exception", "params": {"control_id": AZ_CONTROL, "resource_id": RETAIL_PARTNER}},
                    expected=200).json()
    before = counts_for(ws, "riley")["exceptions"]
    impl = ws.get("cara", "/implementations/impl-az-search-pna", expected=200).json()
    rev = impl["revisions"][-1]
    body = {k: rev[k] for k in ("policy_kind", "source_kind", "source_ref", "pinned_version", "parameters",
                                "assignment_settings", "native_document", "prerequisites", "limitations")}
    new = ws.post("cara", "/implementations/impl-az-search-pna/revisions",
                  {**body, "change_reason": "Tighten assignment"}, expected=201).json()
    r = ws.post("riley", "/workspace/drafts/submit", {"kind": "exception", "basis": draft["basis"],
                                                      "payload": _filled(draft["payload"]), "confirmed": True},
                expected=409).json()
    assert r["error"]["code"] == "DRAFT_STALE"
    assert "implementation_revision_id" in r["error"]["details"]["changed"]
    assert r["error"]["details"]["current_basis"]["implementation_revision_id"] == new["id"]
    assert counts_for(ws, "riley")["exceptions"] == before
    assert audit(ws, "workspace.draft_submitted") == []


def test_rollout_draft_submitted_through_existing_service(ws):
    submit_control_and_impl(ws)
    run = assess(ws)
    draft = ws.post("cara", "/workspace/drafts/prepare",
                    {"kind": "rollout_plan", "params": {"control_id": AZ_CONTROL}}, expected=200).json()
    assert draft["can_submit"] is True, draft["blocking_reasons"]
    assert draft["basis"]["assessment_run_id"] == run["id"] and draft["basis"]["active_plan_id"] is None
    assert draft["basis"]["implementation_revision_id"] == AZ_IMPL_REV
    assert draft["payload"]["control_id"] == AZ_CONTROL and draft["payload"]["rings"]
    body = {"kind": "rollout_plan", "basis": draft["basis"], "payload": draft["payload"], "confirmed": True}

    # A viewer sees why it cannot submit, and the existing service refuses the command.
    vdraft = ws.post("vic", "/workspace/drafts/prepare",
                     {"kind": "rollout_plan", "params": {"control_id": AZ_CONTROL}}, expected=200).json()
    assert vdraft["can_submit"] is False
    assert "Creating a rollout plan requires the CONTROL_ENGINEER role." in vdraft["blocking_reasons"]
    r = ws.post("vic", "/workspace/drafts/submit", body, expected=403).json()
    assert r["error"]["code"] == "FORBIDDEN"
    assert counts_for(ws)["plans"] == 0

    out = ws.post("cara", "/workspace/drafts/submit", body, expected=201).json()
    plan_id = out["created"]["id"]
    assert out["created"]["type"] == "rollout_plan" and re.match(SAFE_HREF, out["created"]["href"])
    plans = ws.get("cara", "/rollout-plans?limit=200", expected=200).json()["items"]
    assert [p["id"] for p in plans] == [plan_id]
    plan = ws.get("cara", f"/rollout-plans/{plan_id}", expected=200).json()
    assert plan["created_by"] == "u-cara" and plan["stage"] == "ASSESSMENT" and plan["state"] == "ACTIVE"
    assert len(audit(ws, "rollout_plan.created", object_id=plan_id)) == 1
    assert len(audit(ws, "workspace.draft_submitted", object_id=plan_id)) == 1

    # The investigation now reflects the persisted plan, and a second draft is blocked.
    status = investigate(ws, "cara", "What is the rollout status of CTL-AZ-SEARCH-PNA?")
    assert views(status)["rollout"]["plan"]["id"] == plan_id
    again = ws.post("cara", "/workspace/drafts/prepare",
                    {"kind": "rollout_plan", "params": {"control_id": AZ_CONTROL}}, expected=200).json()
    assert again["can_submit"] is False and again["basis"]["active_plan_id"] == plan_id
    r = ws.post("cara", "/workspace/drafts/submit", body, expected=409).json()
    assert r["error"]["code"] == "DRAFT_STALE" and r["error"]["details"]["changed"] == ["active_plan_id"]


def test_rollout_draft_stale_after_new_assessment(ws):
    submit_control_and_impl(ws)
    assess(ws)
    draft = ws.post("cara", "/workspace/drafts/prepare",
                    {"kind": "rollout_plan", "params": {"control_id": AZ_CONTROL}}, expected=200).json()
    assert draft["can_submit"] is True
    newer = assess(ws)
    r = ws.post("cara", "/workspace/drafts/submit", {"kind": "rollout_plan", "basis": draft["basis"],
                                                     "payload": draft["payload"], "confirmed": True},
                expected=409).json()
    assert r["error"]["code"] == "DRAFT_STALE"
    assert "assessment_run_id" in r["error"]["details"]["changed"]
    assert r["error"]["details"]["current_basis"]["assessment_run_id"] == newer["id"]
    assert counts_for(ws)["plans"] == 0


def test_rollout_draft_blocked_without_assessment_or_validation(ws):
    draft = ws.post("cara", "/workspace/drafts/prepare",
                    {"kind": "rollout_plan", "params": {"control_id": AZ_CONTROL}}, expected=200).json()
    assert draft["can_submit"] is False
    reasons = " ".join(draft["blocking_reasons"])
    assert "No completed assessment" in reasons and "no passing validation" in reasons
    ws.post("cara", "/workspace/drafts/prepare",
            {"kind": "rollout_plan", "params": {"control_id": AZ_CONTROL, "implementation_revision_id": "irev-nope"}},
            expected=404)


def test_control_draft_reports_duplicates_and_creates_new_controls(ws):
    dup = ws.post("cara", "/workspace/drafts/prepare", {"kind": "control", "params": {
        "problem_statement": "Prevent public network access on Azure AI Search services.", "provider": "azure",
        "resource_type": "Microsoft.Search/searchServices"}}, expected=200).json()
    assert dup["can_submit"] is False
    assert AZ_CONTROL in dup["basis"]["matching_control_ids"]
    assert any("duplicate" in b and AZ_CONTROL in b for b in dup["blocking_reasons"])

    params = {"problem_statement": "Key vaults must not allow public network access.", "provider": "azure",
              "resource_type": "Microsoft.KeyVault/vaults"}
    draft = ws.post("cara", "/workspace/drafts/prepare", {"kind": "control", "params": params}, expected=200).json()
    assert draft["can_submit"] is True and draft["basis"]["matching_control_ids"] == []
    assert draft["payload"]["id"] == "" and "security_objective" in draft["required_fields"]
    vdraft = ws.post("vic", "/workspace/drafts/prepare", {"kind": "control", "params": params}, expected=200).json()
    assert vdraft["can_submit"] is False
    payload = {**draft["payload"], "id": "CTL-AZ-KV-PNA", "name": "Key Vault public network access disabled",
               "security_objective": "Vaults are reachable only through private endpoints.",
               "rationale": "Reduce exposure of secrets.", "applicability_criteria": "All vaults in prod.",
               "security_owner": "Cloud Security", "engineering_owner": "Cloud Engineering",
               "prevention_boundary": "Prevents create/update with public access enabled."}
    body = {"kind": "control", "basis": draft["basis"], "payload": payload, "confirmed": True}
    ws.post("vic", "/workspace/drafts/submit", body, expected=403)
    # The duplicate check is recomputed on the server from the submitted payload, whatever basis is sent.
    dup_payload = {**payload, "id": "CTL-AZ-SEARCH-DUP", "providers": ["azure"],
                   "resource_types": ["Microsoft.Search/searchServices"]}
    for forged in ({}, {**draft["basis"], "matching_control_ids": []}, dup["basis"]):
        r = ws.post("cara", "/workspace/drafts/submit", {**body, "payload": dup_payload, "basis": forged},
                    expected=409).json()
        assert r["error"]["code"] == "DRAFT_BLOCKED" and AZ_CONTROL in r["error"]["details"]["blocking_reasons"][0]
    ws.get("cara", "/controls/CTL-AZ-SEARCH-DUP", expected=404)
    out = ws.post("cara", "/workspace/drafts/submit", body, expected=201).json()
    assert out["created"] == {"type": "control", "id": "CTL-AZ-KV-PNA", "href": "/controls/CTL-AZ-KV-PNA"}
    ctl = ws.get("cara", "/controls/CTL-AZ-KV-PNA", expected=200).json()
    assert ctl["current_revision"]["status"] == "DRAFT"
    # Submitting the same proposal again is now a duplicate of the control it just created.
    r = ws.post("cara", "/workspace/drafts/submit", {**body, "payload": {**payload, "id": "CTL-AZ-KV-PNA-2"}},
                expected=409).json()
    assert r["error"]["code"] == "DRAFT_BLOCKED" and "CTL-AZ-KV-PNA" in r["error"]["details"]["blocking_reasons"][0]
