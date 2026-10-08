"""Server-side authorization primitives.

A role assignment applies to its scope and every descendant scope; a NULL scope means all
scopes. Holding several roles never lets one identity satisfy separation of duties alone,
and ADMIN carries no approval authority and no gate bypass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import Forbidden
from app.models.enums import Role
from app.services.scopes import ScopeTree

ALL = None  # sentinel meaning "every scope"


@dataclass
class Principal:
    user_id: str
    username: str
    display_name: str
    team: str
    # role -> set of scope ids; ALL (None) in the set means global.
    grants: dict[Role, set[str | None]] = field(default_factory=dict)

    @property
    def roles(self) -> list[str]:
        return sorted(r.value for r in self.grants)

    def has_role(self, role: Role) -> bool:
        return role in self.grants

    def has_role_at(self, role: Role, scope_id: str | None, tree: ScopeTree) -> bool:
        scopes = self.grants.get(role)
        if not scopes:
            return False
        if ALL in scopes:
            return True
        if scope_id is None:
            return False
        return any(tree.is_within(scope_id, granted) for granted in scopes if granted)

    def has_role_over_all(self, role: Role, scope_ids: list[str], tree: ScopeTree) -> bool:
        return all(self.has_role_at(role, s, tree) for s in scope_ids)

    def is_global(self) -> bool:
        return any(ALL in scopes for scopes in self.grants.values())

    def readable_scope_ids(self, tree: ScopeTree) -> set[str] | None:
        """Scopes this principal may read. None means all."""
        if self.is_global():
            return None
        result: set[str] = set()
        for scopes in self.grants.values():
            for s in scopes:
                if s and s in tree.scopes:
                    result.update(tree.descendants(s, include_self=True))
                    # Reading ancestors' identity is needed to render hierarchy, but
                    # ancestor-level data is not exposed through this set.
        return result

    def can_read_scope(self, scope_id: str | None, tree: ScopeTree) -> bool:
        readable = self.readable_scope_ids(tree)
        if readable is None:
            return True
        return scope_id is not None and scope_id in readable

    def can_read_all(self, scope_ids: list[str], tree: ScopeTree) -> bool:
        return all(self.can_read_scope(s, tree) for s in scope_ids)


def require_role(principal: Principal, role: Role, *, scope_id: str | None = None, tree: ScopeTree | None = None,
                 action: str = "") -> None:
    if scope_id is None:
        ok = principal.has_role(role)
    else:
        ok = tree is not None and principal.has_role_at(role, scope_id, tree)
    if not ok:
        where = f" at scope {scope_id}" if scope_id else ""
        raise Forbidden(f"{action or 'This action'} requires role {role.value}{where}.")


def require_any_role(principal: Principal, roles: list[Role], *, action: str = "") -> None:
    if not any(principal.has_role(r) for r in roles):
        raise Forbidden(f"{action or 'This action'} requires one of: {', '.join(r.value for r in roles)}.")


def require_global_role(principal: Principal, role: Role, *, action: str = "") -> None:
    scopes = principal.grants.get(role)
    if not scopes or ALL not in scopes:
        raise Forbidden(f"{action or 'This action'} requires organisation-wide role {role.value}.")
