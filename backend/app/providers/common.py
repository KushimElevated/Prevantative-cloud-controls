from __future__ import annotations

import fnmatch
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.clock import iso
from app.core.digests import digest


@dataclass
class Blocker:
    kind: str
    subject_id: str
    scope_id: str | None
    application: str | None
    message: str
    resolution: str
    owner_team: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.subject_id}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        return d


@dataclass
class EvidenceView:
    id: str
    prerequisite: str
    status: str
    collected_at: datetime
    stale: bool
    summary: str
    evidence_ref: str | None
    provided_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prerequisite": self.prerequisite,
            "status": self.status,
            "collected_at": iso(self.collected_at),
            "stale": self.stale,
            "summary": self.summary,
            "evidence_ref": self.evidence_ref,
            "provided_by": self.provided_by,
        }


def short_hash(*parts: Any, length: int = 12) -> str:
    return digest(list(parts)).split(":", 1)[1][:length]


def arn_like(value: str, pattern: str) -> bool:
    """ArnLike-style match: '*' and '?' wildcards, case-sensitive."""
    return fnmatch.fnmatchcase(value, pattern)


def is_stale(collected_at: datetime | None, now: datetime, max_age: timedelta) -> bool:
    return collected_at is None or now - collected_at > max_age
