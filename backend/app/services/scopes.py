"""Explicit scope ancestry resolution (AWS root/OU/account, Azure MG/subscription/RG)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.models.entities import Scope
from app.models.enums import ScopeType

VALID_PARENTS: dict[str, set[str | None]] = {
    ScopeType.AWS_ROOT: {None},
    ScopeType.AWS_OU: {ScopeType.AWS_ROOT, ScopeType.AWS_OU},
    ScopeType.AWS_ACCOUNT: {ScopeType.AWS_ROOT, ScopeType.AWS_OU},
    ScopeType.AZURE_MANAGEMENT_GROUP: {None, ScopeType.AZURE_MANAGEMENT_GROUP},
    ScopeType.AZURE_SUBSCRIPTION: {ScopeType.AZURE_MANAGEMENT_GROUP},
    ScopeType.AZURE_RESOURCE_GROUP: {ScopeType.AZURE_SUBSCRIPTION},
}


@dataclass
class ScopeTree:
    scopes: dict[str, Scope]
    children: dict[str, list[str]]

    @classmethod
    def load(cls, session: Session) -> ScopeTree:
        scopes = {s.id: s for s in session.scalars(select(Scope)).all()}
        children: dict[str, list[str]] = {sid: [] for sid in scopes}
        for s in scopes.values():
            if s.parent_id:
                children.setdefault(s.parent_id, []).append(s.id)
        for v in children.values():
            v.sort()
        return cls(scopes=scopes, children=children)

    def get(self, scope_id: str) -> Scope:
        try:
            return self.scopes[scope_id]
        except KeyError as exc:
            raise NotFound(f"Scope {scope_id} not found") from exc

    def ancestors(self, scope_id: str, include_self: bool = True) -> list[str]:
        """Ordered from the scope itself up to the root."""
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = scope_id
        while current is not None:
            if current in seen:
                raise ValidationFailed(f"Scope cycle detected at {current}")
            seen.add(current)
            chain.append(current)
            current = self.get(current).parent_id
        return chain if include_self else chain[1:]

    def descendants(self, scope_id: str, include_self: bool = True) -> list[str]:
        self.get(scope_id)
        out: list[str] = [scope_id] if include_self else []
        stack = list(reversed(self.children.get(scope_id, [])))
        while stack:
            sid = stack.pop()
            out.append(sid)
            stack.extend(reversed(self.children.get(sid, [])))
        return out

    def is_within(self, scope_id: str, ancestor_id: str) -> bool:
        if scope_id not in self.scopes or ancestor_id not in self.scopes:
            return False
        return ancestor_id in self.ancestors(scope_id)

    def account_or_subscription(self, scope_id: str) -> str | None:
        for sid in self.ancestors(scope_id):
            if self.scopes[sid].scope_type in (ScopeType.AWS_ACCOUNT, ScopeType.AZURE_SUBSCRIPTION):
                return sid
        return None

    def path(self, scope_id: str) -> list[dict]:
        return [
            {"id": s, "native_id": self.scopes[s].native_id, "scope_type": self.scopes[s].scope_type,
             "display_name": self.scopes[s].display_name}
            for s in reversed(self.ancestors(scope_id))
        ]


def validate_scope_parent(scope_type: str, parent: Scope | None) -> None:
    allowed = VALID_PARENTS.get(scope_type)
    if allowed is None:
        raise ValidationFailed(f"Unsupported scope type {scope_type}")
    parent_type = parent.scope_type if parent else None
    if parent_type not in allowed:
        raise ValidationFailed(f"{scope_type} cannot be a child of {parent_type}")


def scope_summary(scope: Scope) -> dict:
    return {
        "id": scope.id,
        "provider": scope.provider,
        "native_id": scope.native_id,
        "scope_type": scope.scope_type,
        "parent_id": scope.parent_id,
        "display_name": scope.display_name,
        "environment": scope.environment,
        "business_owner": scope.business_owner,
        "application": scope.application,
        "is_management_account": scope.is_management_account,
        "provenance": scope.provenance,
    }
