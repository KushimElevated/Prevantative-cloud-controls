"""Model provider abstraction for AI-assisted workspace mode.

A provider runs one bounded investigation: it may call the READ tools it is given (through
``call_tool``, which enforces the principal's authorization and validates every argument) and must
finish with a JSON *plan* matching ``PLAN_SCHEMA``. The plan only selects views, a control and a scope
and adds a summary and recommendations; it never carries data that the canvas displays.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.workspace.catalog import DOMAIN_COMPONENTS

SUGGESTED_ACTIONS = ["run_assessment", "prepare_exception_draft", "prepare_rollout_draft", "prepare_control_draft",
                     "open_classic"]

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "control_id": {"anyOf": [{"type": "string"}, {"type": "null"}],
                       "description": "Catalogue control id the answer is about, or null."},
        "scope_id": {"anyOf": [{"type": "string"}, {"type": "null"}],
                     "description": "Scope id to focus on, or null."},
        "summary": {"type": "string", "description": "Two to four plain-text sentences grounded in tool results."},
        "recommendations": {"type": "array", "items": {"type": "string"},
                            "description": "Up to five plain-text next steps."},
        "views": {"type": "array", "items": {"type": "string", "enum": sorted(DOMAIN_COMPONENTS)},
                  "description": "Canvas components to show, most relevant first."},
        "suggested_actions": {"type": "array", "items": {"type": "string", "enum": SUGGESTED_ACTIONS}},
    },
    "required": ["control_id", "scope_id", "summary", "recommendations", "views", "suggested_actions"],
    "additionalProperties": False,
}

# (tool api name, raw input) -> (result text for the model, is_error)
ToolCaller = Callable[[str, dict[str, Any]], tuple[str, bool]]


@dataclass
class ModelOutcome:
    final_text: str | None
    model: str
    served_model: str | None = None
    stop_reason: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    request_ids: list[str] = field(default_factory=list)


class ProviderUnavailable(RuntimeError):
    pass


class ModelProvider(Protocol):
    name: str
    model: str

    def investigate(self, *, system: str, user: str, tools: list[dict[str, Any]], call_tool: ToolCaller,
                    plan_schema: dict[str, Any], max_tool_calls: int) -> ModelOutcome: ...
