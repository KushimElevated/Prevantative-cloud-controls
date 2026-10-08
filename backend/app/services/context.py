from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.auth.principal import Principal
from app.core.clock import Clock
from app.core.config import Settings
from app.services.scopes import ScopeTree


@dataclass
class RequestContext:
    session: Session
    principal: Principal
    clock: Clock
    settings: Settings
    _now: datetime | None = field(default=None, repr=False)
    _tree: ScopeTree | None = field(default=None, repr=False)

    @property
    def now(self) -> datetime:
        # One consistent "now" per request so gate checks and records agree.
        if self._now is None:
            self._now = self.clock.now()
        return self._now

    @property
    def tree(self) -> ScopeTree:
        if self._tree is None:
            self._tree = ScopeTree.load(self.session)
        return self._tree

    def commit(self) -> None:
        self.session.commit()

    def release_connection(self) -> None:
        """End the (read-only) transaction so the pooled connection is returned while waiting on slow external
        work; the session reconnects lazily on the next query."""
        self.session.rollback()
        self._tree = None

    def refresh_tree(self) -> ScopeTree:
        self._tree = ScopeTree.load(self.session)
        return self._tree
