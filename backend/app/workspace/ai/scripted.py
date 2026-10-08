"""Deterministic stand-in for a model, used by tests and offline demos of the AI-assisted path.

It replays scripted tool calls through the same ``call_tool`` gate a real model would use and then
returns a scripted final text (which may be deliberately invalid to exercise validation and fallback).
"""

from __future__ import annotations

import json
from typing import Any

from app.workspace.ai.base import ModelOutcome, ToolCaller


class ScriptedProvider:
    name = "scripted"

    def __init__(self, tool_calls: list[tuple[str, dict[str, Any]]], final: dict[str, Any] | str | None,
                 *, raise_error: Exception | None = None, model: str = "scripted-test-model"):
        self.tool_calls = tool_calls
        self.final = final
        self.raise_error = raise_error
        self.model = model
        self.seen_tools: list[str] = []
        self.results: list[tuple[str, str, bool]] = []

    def investigate(self, *, system: str, user: str, tools: list[dict[str, Any]], call_tool: ToolCaller,
                    plan_schema: dict[str, Any], max_tool_calls: int) -> ModelOutcome:
        self.seen_tools = [t["name"] for t in tools]
        if self.raise_error is not None:
            raise self.raise_error
        calls = []
        for name, raw in self.tool_calls[:max_tool_calls]:
            text, is_error = call_tool(name, raw)
            self.results.append((name, text, is_error))
            calls.append({"tool": name, "ok": not is_error})
        final = self.final if isinstance(self.final, str) or self.final is None else json.dumps(self.final)
        return ModelOutcome(final_text=final, model=self.model, served_model=self.model, stop_reason="end_turn",
                            tool_calls=calls)
