"""Configurable governance thresholds (no unexplained risk scores).

Values are persisted in governance_settings and changed only through an audited ADMIN
command with optimistic concurrency. Defaults below are conservative design assumptions
that need confirmation with the organisation (see docs/assumptions.md).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, ValidationFailed
from app.models.entities import GovernanceSetting

DEFAULTS: dict[str, dict[str, Any]] = {
    "risk_acceptance": {
        "value": {
            "authority_role": "SECURITY_APPROVER",
            "max_validity_days": {"CRITICAL": 30, "HIGH": 90, "MEDIUM": 180, "LOW": 365},
            "confirmed_with_organisation": False,
        },
        "description": "Role that may accept risk for exceptions and the maximum exception validity per "
        "control severity. Must be confirmed with the organisation.",
    },
    "evidence_freshness": {
        "value": {
            "inventory_max_age_hours": 168,
            "readiness_max_age_days": 30,
            "observation_max_age_hours": 24,
            "assessment_max_age_hours": 72,
            "receipt_max_age_hours": 24,
        },
        "description": "Maximum age of evidence before it is treated as stale.",
    },
    "expiry": {
        "value": {"warning_days": 14},
        "description": "How far ahead upcoming exception expirations are surfaced.",
    },
    "rollout": {
        "value": {
            "min_observation_hours": 0,
            "max_open_blockers_in_enforcing_ring": 0,
            "max_unknown_configuration_in_enforcing_ring": 0,
        },
        "description": "Progression thresholds. Enforcing rings must have no open readiness blockers and no "
        "unknown configuration by default.",
    },
}


def _validate(key: str, value: dict[str, Any]) -> None:
    if key not in DEFAULTS:
        raise ValidationFailed(f"Unknown setting {key}")
    expected = DEFAULTS[key]["value"]
    if set(value) != set(expected):
        raise ValidationFailed(f"Setting {key} must contain exactly: {sorted(expected)}")
    for k, v in value.items():
        if isinstance(expected[k], bool):
            if not isinstance(v, bool):
                raise ValidationFailed(f"{key}.{k} must be a boolean")
        elif isinstance(expected[k], int):
            if not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 100_000:
                raise ValidationFailed(f"{key}.{k} must be a non-negative integer")
        elif isinstance(expected[k], str):
            if k == "authority_role" and v not in {"SECURITY_APPROVER"}:
                raise ValidationFailed("authority_role must be SECURITY_APPROVER in this MVP")
        elif isinstance(expected[k], dict):
            if not isinstance(v, dict) or set(v) != set(expected[k]):
                raise ValidationFailed(f"{key}.{k} must contain {sorted(expected[k])}")


def ensure_defaults(session: Session, now) -> None:
    existing = {s.key for s in session.scalars(select(GovernanceSetting)).all()}
    for key, spec in DEFAULTS.items():
        if key not in existing:
            session.add(GovernanceSetting(key=key, value=spec["value"], description=spec["description"],
                                          updated_at=now, updated_by=None, lock_version=1))


def get(session: Session, key: str) -> dict[str, Any]:
    row = session.get(GovernanceSetting, key)
    return row.value if row else DEFAULTS[key]["value"]


def update(session: Session, key: str, value: dict[str, Any], expected_lock_version: int, actor_id: str, now
           ) -> GovernanceSetting:
    _validate(key, value)
    row = session.get(GovernanceSetting, key, with_for_update=True)
    if row is None:
        raise ValidationFailed(f"Unknown setting {key}")
    if row.lock_version != expected_lock_version:
        raise Conflict("Setting was changed by someone else; reload and retry.",
                       details={"current_lock_version": row.lock_version})
    row.value = value
    row.updated_at = now
    row.updated_by = actor_id
    row.lock_version += 1
    return row
