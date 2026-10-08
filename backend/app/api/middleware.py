"""ASGI middleware: correlation ids, request size/type limits, security headers."""

from __future__ import annotations

import json
import logging
import re
import time

from app.core.context import correlation_id_var
from app.core.ids import new_correlation_id

log = logging.getLogger("ccp.request")
_CID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _error(status: int, code: str, message: str, cid: str):
    body = json.dumps({"error": {"code": code, "message": message, "correlation_id": cid}}).encode()
    return [
        {"type": "http.response.start", "status": status,
         "headers": [(b"content-type", b"application/json"), (b"x-correlation-id", cid.encode())]},
        {"type": "http.response.body", "body": body},
    ]


class RequestGuardMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        incoming = headers.get("x-correlation-id", "")
        cid = incoming if _CID.match(incoming) else new_correlation_id()
        scope.setdefault("state", {})["correlation_id"] = cid
        token = correlation_id_var.set(cid)
        start = time.monotonic()
        status_holder = {"status": 500}
        try:
            method = scope["method"]
            length = headers.get("content-length")
            if length is not None and length.isdigit() and int(length) > self.max_bytes:
                for msg in _error(413, "PAYLOAD_TOO_LARGE", f"Request body exceeds {self.max_bytes} bytes", cid):
                    await send(msg)
                status_holder["status"] = 413
                return
            if method in UNSAFE and length not in (None, "0"):
                ctype = headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    for msg in _error(415, "UNSUPPORTED_MEDIA_TYPE", "Request bodies must be application/json", cid):
                        await send(msg)
                    status_holder["status"] = 415
                    return
            received = 0

            async def limited_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > self.max_bytes:
                        raise _TooLarge()
                return message

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    status_holder["status"] = message["status"]
                    hdrs = list(message.get("headers", []))
                    hdrs += [(b"x-correlation-id", cid.encode()), (b"x-content-type-options", b"nosniff"),
                             (b"referrer-policy", b"no-referrer"), (b"cache-control", b"no-store"),
                             (b"x-frame-options", b"DENY")]
                    message = {**message, "headers": hdrs}
                await send(message)

            try:
                await self.app(scope, limited_receive, send_wrapper)
            except _TooLarge:
                for msg in _error(413, "PAYLOAD_TOO_LARGE", f"Request body exceeds {self.max_bytes} bytes", cid):
                    await send(msg)
                status_holder["status"] = 413
        finally:
            # Never log headers or bodies: they may carry bearer tokens.
            log.info("%s %s %s %.1fms cid=%s", scope.get("method"), scope.get("path"), status_holder["status"],
                     (time.monotonic() - start) * 1000, cid)
            correlation_id_var.reset(token)


class _TooLarge(Exception):
    pass
