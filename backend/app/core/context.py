from __future__ import annotations

from contextvars import ContextVar

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


def current_correlation_id() -> str:
    return correlation_id_var.get()
