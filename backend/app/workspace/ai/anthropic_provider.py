"""Claude (Anthropic API) provider for AI-assisted workspace mode.

Uses the official ``anthropic`` SDK with a manual tool loop so every tool call passes through the
workspace's own authorization and validation gate. Defaults: model ``claude-opus-5-5`` (adaptive
thinking is always on for this model; depth is set with ``output_config.effort``), strict tool schemas
with ``tool_choice`` left at ``auto`` (forced tool choice is rejected by this model), a JSON-schema
final answer, and server-side refusal fallbacks (``fallbacks: "default"``) unless disabled.

The API key is resolved by the SDK from the environment (``ANTHROPIC_API_KEY``); it is never read,
stored or logged here. Errors are reduced to short, secret-free reasons for the caller.
"""

from __future__ import annotations

import logging
from typing import Any

from app.workspace.ai.base import ModelOutcome, ProviderUnavailable, ToolCaller

log = logging.getLogger("ccp.workspace.ai")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TURNS = 12


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, model: str, effort: str, timeout_seconds: float, refusal_fallbacks: bool):
        try:
            import anthropic  # imported lazily: AI mode is optional
        except ImportError as exc:  # pragma: no cover - dependency is locked, kept for slim installs
            raise ProviderUnavailable("The anthropic SDK is not installed.") from exc
        self._anthropic = anthropic
        self.model = model
        self.effort = effort
        self.refusal_fallbacks = refusal_fallbacks
        # One retry on transient errors; the request as a whole is bounded by the timeout.
        self.client = anthropic.Anthropic(timeout=timeout_seconds, max_retries=1)

    def _create(self, **kwargs: Any):
        if self.refusal_fallbacks:
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        return self.client.beta.messages.create(**kwargs)

    def investigate(self, *, system: str, user: str, tools: list[dict[str, Any]], call_tool: ToolCaller,
                    plan_schema: dict[str, Any], max_tool_calls: int) -> ModelOutcome:
        a = self._anthropic
        outcome = ModelOutcome(final_text=None, model=self.model)
        strict_tools = [{**tool, "strict": True} for tool in tools]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        calls = 0
        try:
            for _ in range(MAX_TURNS):
                response = self._create(
                    model=self.model, max_tokens=16000, system=system, tools=strict_tools,
                    tool_choice={"type": "auto"}, messages=messages,
                    output_config={"effort": self.effort,
                                   "format": {"type": "json_schema", "schema": plan_schema}})
                if getattr(response, "_request_id", None):
                    outcome.request_ids.append(response._request_id)
                outcome.served_model = getattr(response, "model", None)
                outcome.stop_reason = response.stop_reason
                if response.stop_reason == "refusal":
                    outcome.error = "The model declined this request."
                    return outcome
                if response.stop_reason == "max_tokens":
                    outcome.error = "The model response was cut off (max_tokens)."
                    return outcome
                if response.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": response.content})
                    continue
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if response.stop_reason == "tool_use" and tool_uses:
                    # Pass the assistant turn back unchanged (thinking blocks included).
                    messages.append({"role": "assistant", "content": response.content})
                    results = []
                    for block in tool_uses:
                        if calls >= max_tool_calls:
                            text, is_error = ("Tool budget exhausted. Answer now with the final JSON plan using the "
                                              "results you already have.", True)
                        else:
                            calls += 1
                            raw = block.input if isinstance(block.input, dict) else {}
                            text, is_error = call_tool(block.name, raw)
                        outcome.tool_calls.append({"tool": block.name, "ok": not is_error})
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": text,
                                        "is_error": is_error})
                    messages.append({"role": "user", "content": results})
                    continue
                outcome.final_text = "".join(b.text for b in response.content if b.type == "text")
                return outcome
            outcome.error = "The model did not finish within the turn limit."
            return outcome
        except a.BadRequestError as exc:
            outcome.error = "The AI provider rejected the request (bad request)."
            log.warning("workspace AI bad request: %s", getattr(exc, "request_id", None))
        except a.AuthenticationError:
            outcome.error = "The AI provider rejected the credentials. Check ANTHROPIC_API_KEY."
        except a.PermissionDeniedError:
            outcome.error = "The AI provider credentials lack permission for this model."
        except a.NotFoundError:
            outcome.error = f"The AI provider does not recognise model {self.model!r}."
        except a.RateLimitError:
            outcome.error = "The AI provider rate limit was reached. Try again shortly."
        except a.APIStatusError as exc:
            outcome.error = f"The AI provider returned an error (HTTP {exc.status_code})."
        except a.APIConnectionError:
            outcome.error = "The AI provider could not be reached (network or timeout)."
        return outcome
