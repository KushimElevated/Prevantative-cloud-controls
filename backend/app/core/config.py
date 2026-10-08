"""Environment-based configuration.

Startup fails closed: a non-local environment cannot run with demo identities, and
no setting can turn on live cloud deployment in this MVP.
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_ENVIRONMENTS = {"local", "test"}


class ConfigurationError(RuntimeError):
    """Raised at startup when configuration is unsafe or unsupported."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_env: str = Field(default="local", description="local | test | production")
    auth_mode: str = Field(default="local-demo", description="local-demo | oidc")
    database_url: str = "postgresql+psycopg://ccp_app:ccp_app_local@localhost:5432/ccp"
    migration_database_url: str = "postgresql+psycopg://ccp_owner:ccp_owner_local@localhost:5432/ccp"
    app_db_role: str = "ccp_app"
    secret_key: str = ""
    token_ttl_minutes: int = 8 * 60
    cors_allowed_origins: str = "http://localhost:3000"
    export_dir: str = "/tmp/ccp-exports"
    max_request_bytes: int = 262_144
    # Delivery seams. Only the local/mock implementations exist in this MVP.
    handoff_adapter: str = "local-export"
    pipeline_mode: str = "mock"
    enable_live_deployment: bool = False
    # Testable clock: ISO-8601 UTC timestamp. Empty means system time.
    app_clock_fixed: str = ""
    log_level: str = "INFO"
    # Optional A2UI-powered AI Control Workspace: a presentation layer over the existing services.
    # Off unless explicitly enabled; the Classic Experience never depends on it.
    enable_a2ui_workspace: bool = False
    # AI-assisted workspace mode. "none" keeps the workspace deterministic (no model calls). The only
    # approved provider is "anthropic"; its API key is read by the SDK from ANTHROPIC_API_KEY and is never
    # stored in settings or logged. Misconfiguration disables AI mode; it never stops the application.
    workspace_ai_provider: str = "none"
    workspace_ai_model: str = "claude-opus-5-5"
    workspace_ai_effort: str = "medium"
    workspace_ai_max_tool_calls: int = 8
    workspace_ai_timeout_seconds: float = 60.0
    workspace_ai_refusal_fallbacks: bool = True
    # Bounds so a slow provider cannot starve the API: overall deadline per investigation and a process-wide cap
    # on concurrent AI investigations (excess requests fall back to deterministic mode immediately).
    workspace_ai_deadline_seconds: float = 120.0
    workspace_ai_max_concurrent: int = 3

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_local(self) -> bool:
        return self.app_env in LOCAL_ENVIRONMENTS


def validate_settings(settings: Settings) -> Settings:
    if settings.enable_live_deployment:
        raise ConfigurationError(
            "ENABLE_LIVE_DEPLOYMENT=true is not supported: this MVP has no cloud mutation path. "
            "Delivery is limited to local bundle export and the clearly labelled mock pipeline."
        )
    if settings.handoff_adapter != "local-export":
        raise ConfigurationError(
            f"HANDOFF_ADAPTER={settings.handoff_adapter!r} is not implemented. Only 'local-export' exists; "
            "pull-request creation is a deferred increment."
        )
    if settings.pipeline_mode != "mock":
        raise ConfigurationError(
            f"PIPELINE_MODE={settings.pipeline_mode!r} is not implemented. Only 'mock' receipts are accepted; "
            "authenticated pipeline callbacks are a deferred increment."
        )
    if settings.app_env not in LOCAL_ENVIRONMENTS | {"production", "staging"}:
        raise ConfigurationError(f"Unknown APP_ENV {settings.app_env!r}")
    if settings.auth_mode == "local-demo":
        if not settings.is_local:
            raise ConfigurationError(
                "AUTH_MODE=local-demo is only permitted when APP_ENV is local or test. "
                "Configure a real identity provider before running outside local demo mode."
            )
    elif settings.auth_mode == "oidc":
        raise ConfigurationError(
            "AUTH_MODE=oidc is not implemented yet (Microsoft Entra ID / OIDC is a deferred increment). "
            "Refusing to start without a working authentication provider."
        )
    else:
        raise ConfigurationError(f"Unknown AUTH_MODE {settings.auth_mode!r}")
    if not settings.secret_key:
        if not settings.is_local:
            raise ConfigurationError("SECRET_KEY must be set outside local/test environments.")
        # Ephemeral per-process key for local demo. Never logged.
        settings.secret_key = secrets.token_urlsafe(48)
    if "*" in settings.cors_origins:
        raise ConfigurationError("Wildcard CORS origins are not allowed; list explicit origins.")
    return settings


@lru_cache
def get_settings() -> Settings:
    return validate_settings(Settings())
