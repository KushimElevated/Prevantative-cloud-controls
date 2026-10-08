from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def new_correlation_id() -> str:
    return uuid.uuid4().hex
