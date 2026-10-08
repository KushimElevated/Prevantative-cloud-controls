"""A2UI v0.9 message construction and server-side validation.

Every payload the workspace returns passes ``validate_messages`` first, whatever produced it. The
validator enforces the envelope, the approved catalog, strict component props, the adjacency-list
structure (one root, known children, no cycles, no orphans), safe data-model paths and the per-component
data contracts. Anything else is rejected with a list of issues; nothing is repaired silently.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.workspace.catalog import (
    ALL_COMPONENTS,
    CATALOG_ID,
    COMPONENT_ID,
    DOMAIN_COMPONENTS,
    PROTOCOL_VERSION,
    props_model,
)

MESSAGE_KINDS = ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")
SURFACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
POINTER = re.compile(r"^(/|(/[A-Za-z0-9_-]{1,64}){1,4})$")
MAX_MESSAGES = 20
MAX_COMPONENTS = 60
MAX_DATA_BYTES = 512_000
_COMPONENT_ID = re.compile(COMPONENT_ID)


class A2uiValidationError(ValueError):
    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues[:10]))
        self.issues = issues


@dataclass
class _Surface:
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    data: Any = field(default_factory=dict)


def _set_pointer(root: Any, pointer: str, value: Any) -> Any:
    if pointer == "/":
        return value
    parts = pointer.strip("/").split("/")
    node = root if isinstance(root, dict) else {}
    out = node
    for p in parts[:-1]:
        nxt = node.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            node[p] = nxt
        node = nxt
    node[parts[-1]] = value
    return out


def _get_pointer(root: Any, pointer: str) -> Any:
    node = root
    for p in pointer.strip("/").split("/"):
        if not isinstance(node, dict) or p not in node:
            return None
        node = node[p]
    return node


def _issues(prefix: str, exc: ValidationError) -> list[str]:
    return [f"{prefix}.{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]


def validate_messages(messages: Any, known_surfaces: set[str] | None = None) -> list[dict[str, Any]]:
    """Validate a batch of server-to-client messages. Returns the messages unchanged or raises."""
    issues: list[str] = []
    if not isinstance(messages, list) or not messages:
        raise A2uiValidationError(["messages must be a non-empty list"])
    if len(messages) > MAX_MESSAGES:
        raise A2uiValidationError([f"too many messages ({len(messages)} > {MAX_MESSAGES})"])
    surfaces: dict[str, _Surface] = {s: _Surface() for s in (known_surfaces or set())}
    for i, msg in enumerate(messages):
        where = f"messages[{i}]"
        if not isinstance(msg, dict):
            issues.append(f"{where}: not an object")
            continue
        if msg.get("version") != PROTOCOL_VERSION:
            issues.append(f"{where}.version: must be {PROTOCOL_VERSION!r}")
        kinds = [k for k in msg if k != "version"]
        if len(kinds) != 1 or kinds[0] not in MESSAGE_KINDS:
            issues.append(f"{where}: must contain exactly one of {', '.join(MESSAGE_KINDS)}")
            continue
        kind = kinds[0]
        body = msg[kind]
        if not isinstance(body, dict):
            issues.append(f"{where}.{kind}: not an object")
            continue
        sid = body.get("surfaceId")
        if not isinstance(sid, str) or not SURFACE_ID.match(sid):
            issues.append(f"{where}.{kind}.surfaceId: invalid")
            continue
        if kind == "createSurface":
            extra = set(body) - {"surfaceId", "catalogId", "sendDataModel"}
            if extra:
                issues.append(f"{where}.createSurface: unsupported fields {sorted(extra)}")
            if body.get("catalogId") != CATALOG_ID:
                issues.append(f"{where}.createSurface.catalogId: only {CATALOG_ID!r} is accepted")
            if body.get("sendDataModel", False) is not False:
                issues.append(f"{where}.createSurface.sendDataModel: must be false")
            if sid in surfaces:
                issues.append(f"{where}.createSurface: surface {sid!r} already exists")
            surfaces[sid] = _Surface()
            continue
        if sid not in surfaces:
            issues.append(f"{where}.{kind}: surface {sid!r} was not created")
            continue
        surface = surfaces[sid]
        if kind == "deleteSurface":
            if set(body) != {"surfaceId"}:
                issues.append(f"{where}.deleteSurface: unsupported fields")
            del surfaces[sid]
        elif kind == "updateDataModel":
            extra = set(body) - {"surfaceId", "path", "value"}
            if extra:
                issues.append(f"{where}.updateDataModel: unsupported fields {sorted(extra)}")
            path = body.get("path", "/")
            if not isinstance(path, str) or not POINTER.match(path):
                issues.append(f"{where}.updateDataModel.path: invalid JSON pointer")
                continue
            try:
                size = len(json.dumps(body.get("value"), allow_nan=False))
            except (TypeError, ValueError):
                issues.append(f"{where}.updateDataModel.value: not plain JSON")
                continue
            if size > MAX_DATA_BYTES:
                issues.append(f"{where}.updateDataModel.value: too large ({size} bytes)")
                continue
            if path == "/" and not isinstance(body.get("value"), dict):
                issues.append(f"{where}.updateDataModel.value: root data model must be an object")
                continue
            surface.data = _set_pointer(surface.data, path, body.get("value"))
        else:  # updateComponents
            if set(body) != {"surfaceId", "components"}:
                issues.append(f"{where}.updateComponents: must contain exactly surfaceId and components")
            comps = body.get("components")
            if not isinstance(comps, list) or not comps:
                issues.append(f"{where}.updateComponents.components: must be a non-empty list")
                continue
            if len(comps) + len(surface.components) > MAX_COMPONENTS:
                issues.append(f"{where}.updateComponents: too many components")
                continue
            for j, comp in enumerate(comps):
                cw = f"{where}.updateComponents.components[{j}]"
                if not isinstance(comp, dict):
                    issues.append(f"{cw}: not an object")
                    continue
                cid, ctype = comp.get("id"), comp.get("component")
                if not isinstance(cid, str) or not _COMPONENT_ID.match(cid):
                    issues.append(f"{cw}.id: invalid component id")
                    continue
                if ctype not in ALL_COMPONENTS:
                    issues.append(f"{cw}.component: {ctype!r} is not in the approved catalog")
                    continue
                props = {k: v for k, v in comp.items() if k not in ("id", "component")}
                try:
                    props_model(ctype).model_validate(props)
                except ValidationError as exc:
                    issues.extend(_issues(f"{cw}({ctype})", exc))
                    continue
                surface.components[cid] = comp
    for sid, surface in surfaces.items():
        if surface.components:
            issues.extend(_check_tree(sid, surface))
    if issues:
        raise A2uiValidationError(issues)
    return messages


def _check_tree(sid: str, surface: _Surface) -> list[str]:
    comps = surface.components
    issues: list[str] = []
    root = comps.get("root")
    if root is None:
        return [f"surface {sid}: no component with id 'root'"]
    if root["component"] != "CanvasStack":
        issues.append(f"surface {sid}: root must be a CanvasStack")
    seen: set[str] = set()

    def visit(cid: str, stack: tuple[str, ...]) -> None:
        if cid in stack:
            issues.append(f"surface {sid}: cycle through {cid!r}")
            return
        if cid not in comps:
            issues.append(f"surface {sid}: unknown child {cid!r}")
            return
        if cid in seen:
            issues.append(f"surface {sid}: component {cid!r} has more than one parent")
            return
        seen.add(cid)
        for child in comps[cid].get("children", []) if comps[cid]["component"] == "CanvasStack" else []:
            visit(child, stack + (cid,))

    visit("root", ())
    orphans = sorted(set(comps) - seen)
    if orphans:
        issues.append(f"surface {sid}: components not reachable from root: {orphans}")
    for cid, comp in comps.items():
        if comp["component"] not in DOMAIN_COMPONENTS:
            continue
        path = comp["data"]["path"]
        value = _get_pointer(surface.data, path)
        if value is None:
            issues.append(f"surface {sid}: {cid} data at {path} is missing")
            continue
        try:
            DOMAIN_COMPONENTS[comp["component"]].model_validate(value)
        except ValidationError as exc:
            issues.extend(_issues(f"surface {sid}: {cid}({comp['component']}) data", exc))
    return issues


def build_surface(surface_id: str, sections: list[dict[str, Any]], views: dict[str, Any],
                  notices: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Build a complete surface. ``sections`` items: {"component", "title", "view"} in display order."""
    children: list[str] = []
    components: list[dict[str, Any]] = []
    for n, notice in enumerate(notices or []):
        cid = f"notice_{n}"
        children.append(cid)
        components.append({"id": cid, "component": "CanvasNotice", "tone": notice["tone"], "text": notice["text"]})
    for section in sections:
        cid = f"c_{section['view']}"
        children.append(cid)
        components.append({"id": cid, "component": section["component"], "title": section["title"],
                           "data": {"path": f"/views/{section['view']}"}})
    root = {"id": "root", "component": "CanvasStack", "children": children}
    used = {s["view"] for s in sections}
    return [
        {"version": PROTOCOL_VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": CATALOG_ID}},
        {"version": PROTOCOL_VERSION, "updateDataModel": {
            "surfaceId": surface_id, "path": "/", "value": {"views": {k: v for k, v in views.items() if k in used}}}},
        {"version": PROTOCOL_VERSION, "updateComponents": {"surfaceId": surface_id,
                                                          "components": [root, *components]}},
    ]
