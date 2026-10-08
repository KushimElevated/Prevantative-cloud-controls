"""Canonical JSON and content digests.

Canonical form: UTF-8 JSON with sorted keys, no insignificant whitespace, no NaN.
Floats are rejected so that digests never depend on float formatting.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise ValueError("floats are not permitted in canonical documents; use strings or integers")
    if isinstance(value, dict):
        for v in value.values():
            _reject_floats(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _reject_floats(v)


def canonical_json(value: Any) -> str:
    _reject_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(value: Any) -> str:
    return sha256_text(canonical_json(value))


def pretty_json(value: Any) -> str:
    """Deterministic human-readable JSON used for exported files."""
    _reject_floats(value)
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
