"""Integration test harness against the real PostgreSQL schema.

The test database is migrated from scratch (as the owner role) and seeded once per session
(as the application role). Each test runs inside an outer transaction on a single connection
that is rolled back afterwards; application commits become savepoints. The audit/immutability
triggers and role privileges are therefore exercised exactly as in the running app.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import db
from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app
from app.seed.seed import seed

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://ccp_app:ccp_app_local@localhost:5432/ccp_test")
TEST_MIGRATION_DB = os.environ.get(
    "TEST_MIGRATION_DATABASE_URL", "postgresql+psycopg://ccp_owner:ccp_owner_local@localhost:5432/ccp_test")
ANCHOR = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
NOW = ANCHOR + timedelta(minutes=30)


def make_settings(export_dir: str) -> Settings:
    return Settings(app_env="test", auth_mode="local-demo", database_url=TEST_DB, secret_key="test-secret-key",
                    export_dir=export_dir, cors_allowed_origins="http://localhost:3000")


@pytest.fixture(scope="session")
def engine(tmp_path_factory):
    os.environ["MIGRATION_DATABASE_URL"] = TEST_MIGRATION_DB
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(TEST_DB, future=True)
    with Session(eng) as session:
        seed(session, ANCHOR, make_settings(str(tmp_path_factory.mktemp("seed-exports"))))
        session.commit()
    yield eng
    eng.dispose()


@pytest.fixture
def clock():
    return FixedClock(NOW)


@pytest.fixture
def connection(engine):
    conn = engine.connect()
    trans = conn.begin()
    yield conn
    trans.rollback()
    conn.close()


@pytest.fixture
def app(connection, clock, tmp_path):
    application = create_app(make_settings(str(tmp_path)), clock)
    db._engine.dispose()  # noqa: SLF001 - tests route every session through the transactional connection
    db._session_factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint",  # noqa: SLF001
                                       expire_on_commit=False)
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class Api:
    """Small helper that logs in demo users through the real login endpoint."""

    def __init__(self, client: TestClient):
        self.client = client
        self._tokens: dict[str, str] = {}

    def login(self, username: str) -> dict[str, str]:
        r = self.client.post("/api/v1/auth/dev-login", json={"username": username})
        assert r.status_code == 200, r.text
        self._tokens[username] = r.json()["token"]
        return {"Authorization": f"Bearer {self._tokens[username]}"}

    def call(self, username: str, method: str, path: str, json=None, expected: int | tuple | None = None,
             **kwargs):
        headers = {"Authorization": f"Bearer {self._tokens[username]}"} if username in self._tokens \
            else self.login(username)
        r = self.client.request(method, f"/api/v1{path}", json=json, headers=headers, **kwargs)
        if r.status_code == 401:
            headers = self.login(username)
            r = self.client.request(method, f"/api/v1{path}", json=json, headers=headers, **kwargs)
        if expected is not None:
            codes = expected if isinstance(expected, tuple) else (expected,)
            assert r.status_code in codes, f"{method} {path} -> {r.status_code}: {r.text}"
        return r

    def get(self, user, path, **kw):
        return self.call(user, "GET", path, **kw)

    def post(self, user, path, json=None, **kw):
        return self.call(user, "POST", path, json=json, **kw)

    def put(self, user, path, json=None, **kw):
        return self.call(user, "PUT", path, json=json, **kw)


@pytest.fixture
def api(client):
    return Api(client)
