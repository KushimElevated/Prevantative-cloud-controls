"""Operational commands.

    python -m app.cli migrate            # alembic upgrade head (owner/migration role)
    python -m app.cli seed [--anchor ISO]
    python -m app.cli reset [--anchor ISO]   # downgrade to base, upgrade, seed (repeatable)
    python -m app.cli reconcile          # idempotent expiry/drift reconciliation (scheduling seam)
    python -m app.cli check-config       # fail-fast configuration validation
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.core import db
from app.core.clock import build_clock, parse_utc
from app.core.config import ConfigurationError, Settings, validate_settings

BACKEND = Path(__file__).resolve().parent.parent


def _alembic(settings: Settings) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    os.environ.setdefault("MIGRATION_DATABASE_URL", settings.migration_database_url)
    os.environ.setdefault("APP_DB_ROLE", settings.app_db_role)
    return cfg


def _anchor(settings: Settings, raw: str | None):
    from app.seed.seed import default_anchor
    return parse_utc(raw) if raw else default_anchor(build_clock(settings.app_clock_fixed).now())


def cmd_migrate(settings: Settings, _args) -> None:
    command.upgrade(_alembic(settings), "head")
    print("migrations applied")


def cmd_seed(settings: Settings, args) -> None:
    from app.seed.seed import seed
    db.init_engine(settings.database_url)
    with Session(db.get_engine()) as session:
        try:
            result = seed(session, _anchor(settings, args.anchor), settings)
        except RuntimeError as exc:
            print(f"seed refused: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        session.commit()
    print(json.dumps({"seeded": result}))


def cmd_reset(settings: Settings, args) -> None:
    cfg = _alembic(settings)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    export_dir = Path(settings.export_dir)
    if export_dir.exists() and export_dir.is_dir():
        for child in export_dir.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    cmd_seed(settings, args)


def cmd_reconcile(settings: Settings, _args) -> None:
    from app.services.reconcile import reconcile
    db.init_engine(settings.database_url)
    clock = build_clock(settings.app_clock_fixed)
    with Session(db.get_engine()) as session:
        summary = reconcile(session, clock.now(), actor=None)
        session.commit()
    print(json.dumps({"reconcile": {k: v for k, v in summary.items()}}, indent=2))


def cmd_check_config(settings: Settings, _args) -> None:
    print(json.dumps({"app_env": settings.app_env, "auth_mode": settings.auth_mode,
                      "handoff_adapter": settings.handoff_adapter, "pipeline_mode": settings.pipeline_mode,
                      "live_deployment": settings.enable_live_deployment, "status": "ok"}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ccp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    for name in ("seed", "reset"):
        p = sub.add_parser(name)
        p.add_argument("--anchor", help="ISO-8601 UTC anchor for relative fixture timestamps")
    sub.add_parser("reconcile")
    sub.add_parser("check-config")
    args = parser.parse_args(argv)
    try:
        settings = validate_settings(Settings())
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    {"migrate": cmd_migrate, "seed": cmd_seed, "reset": cmd_reset, "reconcile": cmd_reconcile,
     "check-config": cmd_check_config}[args.command](settings, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
