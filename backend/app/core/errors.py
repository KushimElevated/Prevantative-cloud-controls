"""Structured domain errors mapped to HTTP responses."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, details: Any = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class Forbidden(DomainError):
    status_code = 403
    code = "FORBIDDEN"


class Unauthenticated(DomainError):
    status_code = 401
    code = "UNAUTHENTICATED"


class Conflict(DomainError):
    status_code = 409
    code = "CONFLICT"


class GateFailed(DomainError):
    status_code = 409
    code = "GATE_FAILED"


class ValidationFailed(DomainError):
    status_code = 422
    code = "VALIDATION_FAILED"


class Unsupported(DomainError):
    status_code = 422
    code = "UNSUPPORTED"
