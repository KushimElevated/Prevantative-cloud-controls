from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def init_engine(database_url: str) -> Engine:
    global _engine, _session_factory
    _engine = create_engine(database_url, pool_pre_ping=True, future=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database engine not initialised")
    return _engine


def session_scope() -> Iterator[Session]:
    """One transaction per request: commit on success, roll back on any error."""
    if _session_factory is None:
        raise RuntimeError("database engine not initialised")
    session = _session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
