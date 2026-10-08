"""Server-side A2UI validation: envelope, approved catalog, strict props, tree structure and data contracts.

Pure function tests (no database): every workspace payload passes ``validate_messages`` before it is
returned, so anything the validator accepts is what the browser may render.
"""

from __future__ import annotations

import copy
import json
import os
import re

import pytest

from app.workspace.a2ui import A2uiValidationError, build_surface, validate_messages
from app.workspace.catalog import ALL_COMPONENTS, CATALOG_ID, DOMAIN_COMPONENTS, SAFE_HREF, catalog_document

SID = "ws-test"
IMPACT = {"available": False, "unavailable_reason": "No completed impact assessment covers this scope.",
          "can_run": True, "run_action": {"implementation_revision_id": "irev-1", "target_scope_id": "az-mg-prod"},
          "links": [{"label": "Open control", "href": "/controls/CTL-AZ-SEARCH-PNA"}]}
SCOPE = {"provider": "azure", "selected_scope_id": "az-mg-prod",
         "options": [{"scope_id": "az-mg-prod", "name": "Production MG", "scope_type": "AZURE_MANAGEMENT_GROUP",
                      "environment": "prod", "depth": 1}], "note": "Only readable scopes are listed."}


def valid() -> list[dict]:
    return build_surface(SID, [{"component": "ImpactAssessment", "title": "Impact assessment", "view": "impact"},
                               {"component": "ScopeSelector", "title": "Scope", "view": "scope"}],
                         {"impact": copy.deepcopy(IMPACT), "scope": copy.deepcopy(SCOPE), "unused": {"x": 1}},
                         notices=[{"tone": "warning", "text": "Impact is UNKNOWN."}])


def components(msgs: list[dict]) -> list[dict]:
    return next(m["updateComponents"]["components"] for m in msgs if "updateComponents" in m)


def comp(msgs: list[dict], cid: str) -> dict:
    return next(c for c in components(msgs) if c["id"] == cid)


def data(msgs: list[dict]) -> dict:
    return next(m["updateDataModel"]["value"] for m in msgs if "updateDataModel" in m)


def rejected(msgs, match: str, known: set[str] | None = None) -> list[str]:
    with pytest.raises(A2uiValidationError) as exc:
        validate_messages(msgs, known)
    assert any(re.search(match, i) for i in exc.value.issues), exc.value.issues
    return exc.value.issues


# ------------------------------------------------------------------------------------ accepted


def test_build_surface_output_is_valid_and_minimal():
    msgs = valid()
    assert validate_messages(msgs) is msgs
    assert [next(k for k in m if k != "version") for m in msgs] == ["createSurface", "updateDataModel",
                                                                   "updateComponents"]
    assert all(m["version"] == "v0.9" for m in msgs)
    assert msgs[0]["createSurface"] == {"surfaceId": SID, "catalogId": CATALOG_ID}
    # Only views that are bound by a component are sent.
    assert sorted(data(msgs)["views"]) == ["impact", "scope"]
    root = comp(msgs, "root")
    assert root["component"] == "CanvasStack" and root["children"] == ["notice_0", "c_impact", "c_scope"]
    assert comp(msgs, "c_impact") == {"id": "c_impact", "component": "ImpactAssessment",
                                      "title": "Impact assessment", "data": {"path": "/views/impact"}}


def test_delete_of_a_known_surface_and_partial_updates_are_accepted():
    validate_messages([{"version": "v0.9", "deleteSurface": {"surfaceId": SID}}], known_surfaces={SID})
    msgs = valid()
    msgs.insert(2, {"version": "v0.9", "updateDataModel": {"surfaceId": SID, "path": "/views/scope",
                                                           "value": copy.deepcopy(SCOPE)}})
    validate_messages(msgs)


def test_catalog_has_only_domain_and_layout_components_and_matches_generated_doc():
    assert set(ALL_COMPONENTS) == set(DOMAIN_COMPONENTS) | {"CanvasStack", "CanvasNotice"}
    assert not {"Text", "Button", "Image", "TextField", "Row", "Column", "Card", "Modal"} & set(ALL_COMPONENTS)
    doc = catalog_document()
    assert doc["catalogId"] == CATALOG_ID and doc["protocolVersion"] == "v0.9"
    assert set(doc["clientActions"]) == {"ws.select_scope", "ws.run_assessment", "ws.prepare_exception_draft"}
    for name in DOMAIN_COMPONENTS:
        props = doc["components"][name]["props_schema"]
        assert props["additionalProperties"] is False and set(props["properties"]) == {"title", "data"}
        assert doc["components"][name]["data_schema"]["additionalProperties"] is False
    path = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "a2ui-catalog.json")
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == json.loads(json.dumps(doc)), "docs/a2ui-catalog.json is out of date"


@pytest.mark.parametrize("href,ok", [
    ("/controls/CTL-AZ-SEARCH-PNA", True),
    ("/exceptions?control_id=CTL-AZ-SEARCH-PNA", True),
    ("/assessments/asmt_e667b12a7b0d4b41998d", True),
    ("/workspace", True),
    ("javascript:alert(1)", False),
    ("https://evil.example/x", False),
    ("//evil.example/x", False),
    ("/settings", False),
    ("/controls/x\"onmouseover=\"y", False),
    ("/controls/<script>", False),
])
def test_safe_href_pattern(href, ok):
    assert bool(re.match(SAFE_HREF, href)) is ok


# ------------------------------------------------------------------------------------ envelope


def test_rejects_wrong_version():
    msgs = valid()
    msgs[0]["version"] = "v0.8"
    rejected(msgs, r"messages\[0\]\.version")
    msgs = valid()
    del msgs[2]["version"]
    rejected(msgs, r"messages\[2\]\.version")


def test_rejects_two_message_kinds_in_one_message():
    msgs = valid()
    msgs[1]["deleteSurface"] = {"surfaceId": SID}
    rejected(msgs, r"messages\[1\]: must contain exactly one of")


@pytest.mark.parametrize("bad", [None, [], "x", 1, [1], [{"version": "v0.9", "beginRendering": {"surfaceId": SID}}]])
def test_rejects_malformed_batches(bad):
    with pytest.raises(A2uiValidationError):
        validate_messages(bad)


def test_rejects_too_many_messages_and_bad_surface_ids():
    rejected(valid() * 7, r"too many messages")
    msgs = valid()
    for m in msgs:
        next(v for k, v in m.items() if k != "version")["surfaceId"] = "bad surface/../id"
    rejected(msgs, r"surfaceId: invalid")


@pytest.mark.parametrize("catalog", [
    "https://a2ui.org/specification/v0_9/basic_catalog.json", "urn:ccp:a2ui:control-workspace:v2", "", None])
def test_rejects_other_catalogs(catalog):
    msgs = valid()
    msgs[0]["createSurface"]["catalogId"] = catalog
    rejected(msgs, r"catalogId: only")


def test_rejects_send_data_model_and_theme():
    msgs = valid()
    msgs[0]["createSurface"]["sendDataModel"] = True
    rejected(msgs, r"sendDataModel: must be false")
    msgs = valid()
    msgs[0]["createSurface"]["theme"] = {"primaryColor": "#ff0000"}
    rejected(msgs, r"unsupported fields \['theme'\]")


def test_rejects_messages_for_surfaces_that_were_not_created():
    rejected([{"version": "v0.9", "deleteSurface": {"surfaceId": "nope"}}], r"surface 'nope' was not created")
    msgs = valid()
    rejected([msgs[1], msgs[0], msgs[2]], r"messages\[0\]\.updateDataModel: surface 'ws-test' was not created")
    msgs = valid()
    msgs.insert(1, {"version": "v0.9", "deleteSurface": {"surfaceId": SID}})
    rejected(msgs, r"was not created")
    msgs = valid()
    rejected(msgs + [msgs[0]], r"already exists")


def test_rejects_unexpected_fields_on_update_messages():
    msgs = valid()
    msgs[2]["updateComponents"]["root"] = "c_impact"
    rejected(msgs, r"must contain exactly surfaceId and components")
    msgs = valid()
    msgs[1]["updateDataModel"]["op"] = "replace"
    rejected(msgs, r"unsupported fields \['op'\]")
    msgs = valid()
    msgs[1]["updateDataModel"]["path"] = "/views/../../etc"
    rejected(msgs, r"invalid JSON pointer")


# ------------------------------------------------------------------------------------ components


@pytest.mark.parametrize("component", [
    {"id": "x", "component": "Script", "src": "https://evil.example/x.js"},
    {"id": "x", "component": "Text", "text": "hello"},
    {"id": "x", "component": "Button", "child": "c_impact", "action": {"event": {"name": "ws.run_assessment"}}},
    {"id": "x", "component": "Html", "html": "<img src=x onerror=alert(1)>"},
    {"id": "x", "component": None},
])
def test_rejects_components_outside_the_catalog(component):
    msgs = valid()
    components(msgs).append(component)
    comp(msgs, "root")["children"].append("x")
    rejected(msgs, r"is not in the approved catalog")


@pytest.mark.parametrize("extra", [
    {"onClick": "alert(1)"}, {"style": {"color": "red"}}, {"html": "<b>x</b>"}, {"href": "/controls"},
    {"action": {"event": {"name": "ws.run_assessment"}}}, {"children": ["c_scope"]},
])
def test_rejects_extra_props_on_domain_components(extra):
    msgs = valid()
    comp(msgs, "c_impact").update(extra)
    rejected(msgs, rf"\(ImpactAssessment\)\.{next(iter(extra))}")


def test_rejects_extra_props_on_layout_components():
    msgs = valid()
    comp(msgs, "notice_0")["markdown"] = True
    rejected(msgs, r"\(CanvasNotice\)\.markdown")
    msgs = valid()
    comp(msgs, "notice_0")["tone"] = "success"
    rejected(msgs, r"\(CanvasNotice\)\.tone")
    msgs = valid()
    comp(msgs, "root")["direction"] = "row"
    rejected(msgs, r"\(CanvasStack\)\.direction")


@pytest.mark.parametrize("title", [{"path": "/views/impact"}, 123, None, "", "x" * 121, ["a"]])
def test_rejects_title_that_is_not_plain_text(title):
    msgs = valid()
    comp(msgs, "c_impact")["title"] = title
    rejected(msgs, r"\(ImpactAssessment\)\.title")


@pytest.mark.parametrize("binding", [
    {"path": "/secrets/token"}, {"path": "/views/../secrets"}, {"path": "/views/Impact"}, {"path": "/"},
    {"path": "/views/impact", "literal": True}, {"available": True, "links": []}, "/views/impact",
])
def test_rejects_data_bindings_outside_views(binding):
    msgs = valid()
    comp(msgs, "c_impact")["data"] = binding
    rejected(msgs, r"\(ImpactAssessment\)\.data")


@pytest.mark.parametrize("cid", ["1bad", "has space", "<script>", "", "x" * 65, 7])
def test_rejects_invalid_component_ids(cid):
    msgs = valid()
    comp(msgs, "c_scope")["id"] = cid
    rejected(msgs, r"\.id: invalid component id")


# ------------------------------------------------------------------------------------ structure


def test_rejects_missing_root():
    msgs = valid()
    comp(msgs, "root")["id"] = "top"
    rejected(msgs, r"no component with id 'root'")


def test_rejects_root_that_is_not_a_stack():
    msgs = valid()
    root = comp(msgs, "root")
    root.clear()
    root.update({"id": "root", "component": "CanvasNotice", "tone": "info", "text": "hi"})
    rejected(msgs, r"root must be a CanvasStack")


def test_rejects_unknown_child():
    msgs = valid()
    comp(msgs, "root")["children"].append("ghost")
    rejected(msgs, r"unknown child 'ghost'")


def test_rejects_cycles():
    msgs = valid()
    components(msgs).append({"id": "inner", "component": "CanvasStack", "children": ["root"]})
    comp(msgs, "root")["children"].append("inner")
    rejected(msgs, r"cycle through 'root'")
    msgs = valid()
    components(msgs).append({"id": "inner", "component": "CanvasStack", "children": ["inner"]})
    comp(msgs, "root")["children"].append("inner")
    rejected(msgs, r"cycle through 'inner'")


def test_rejects_orphans():
    msgs = valid()
    components(msgs).append({"id": "lonely", "component": "CanvasNotice", "tone": "info", "text": "not shown"})
    rejected(msgs, r"not reachable from root: \['lonely'\]")


def test_rejects_component_with_two_parents():
    msgs = valid()
    components(msgs).append({"id": "inner", "component": "CanvasStack", "children": ["c_impact"]})
    comp(msgs, "root")["children"].append("inner")
    rejected(msgs, r"'c_impact' has more than one parent")


def test_rejects_too_many_components():
    msgs = valid()
    extra = [{"id": f"n{i}", "component": "CanvasNotice", "tone": "info", "text": "x"} for i in range(60)]
    components(msgs).extend(extra)
    rejected(msgs, r"too many components")


# ------------------------------------------------------------------------------------ data model


def test_rejects_missing_data_model_value():
    msgs = valid()
    del data(msgs)["views"]["impact"]
    rejected(msgs, r"c_impact data at /views/impact is missing")
    msgs = valid()
    msgs.pop(1)
    rejected(msgs, r"is missing")


@pytest.mark.parametrize("mutate,match", [
    (lambda v: v["links"].append({"label": "x", "href": "javascript:alert(1)"}), r"links\.1\.href"),
    (lambda v: v["links"].append({"label": "x", "href": "https://evil.example"}), r"links\.1\.href"),
    (lambda v: v["links"].append({"label": "x", "href": "//evil.example/a"}), r"links\.1\.href"),
    (lambda v: v["links"][0].update(target="_blank"), r"links\.0\.target"),
    (lambda v: v.update(script="alert(1)"), r"\.script"),
    (lambda v: v.update(available={"x": 1}), r"\.available"),
    (lambda v: v.pop("links"), r"\.links"),
    (lambda v: v.update(configuration={"COMPLIANT": "many"}), r"configuration\.COMPLIANT"),
    (lambda v: v["run_action"].update(command="rm -rf /"), r"run_action\.command"),
])
def test_rejects_data_violating_the_component_contract(mutate, match):
    msgs = valid()
    mutate(data(msgs)["views"]["impact"])
    rejected(msgs, r"c_impact\(ImpactAssessment\) data.*" + match)


def test_rejects_data_violating_another_contract():
    msgs = valid()
    data(msgs)["views"]["scope"]["options"][0]["depth"] = "deep"
    rejected(msgs, r"c_scope\(ScopeSelector\) data\.options\.0\.depth")


def test_rejects_oversized_and_non_json_data():
    msgs = valid()
    data(msgs)["views"]["junk"] = "x" * 600_000
    rejected(msgs, r"too large")
    msgs = valid()
    data(msgs)["views"]["impact"]["total_resources"] = float("nan")
    rejected(msgs, r"not plain JSON")
    msgs = valid()
    data(msgs)["views"]["impact"]["when"] = object()
    rejected(msgs, r"not plain JSON")
    msgs = valid()
    msgs[1]["updateDataModel"]["value"] = ["not", "an", "object"]
    rejected(msgs, r"root data model must be an object")
