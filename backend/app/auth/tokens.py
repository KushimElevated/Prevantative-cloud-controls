"""Signed bearer tokens for local demo identities.

Tokens carry only the user id and expiry. Roles and scopes are always loaded from the
database on each request; nothing about authority is trusted from the client.
Bearer tokens (not cookies) are used, so cross-site request forgery does not apply.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta

from app.core.errors import Unauthenticated


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(user_id: str, secret: str, now: datetime, ttl_minutes: int) -> str:
    payload = {"sub": user_id, "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()), "kind": "local-demo"}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str, secret: str, now: datetime) -> str:
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise Unauthenticated("Malformed token") from exc
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise Unauthenticated("Invalid token signature")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise Unauthenticated("Malformed token") from exc
    if payload.get("kind") != "local-demo" or not isinstance(payload.get("sub"), str):
        raise Unauthenticated("Unsupported token")
    if int(payload.get("exp", 0)) < int(now.timestamp()):
        raise Unauthenticated("Token expired")
    return payload["sub"]
