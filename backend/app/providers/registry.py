from __future__ import annotations

from app.core.errors import Unsupported
from app.providers.aws_scp import AwsScpProvider
from app.providers.azure_policy import AzurePolicyProvider

_PROVIDERS = {"azure": AzurePolicyProvider(), "aws": AwsScpProvider()}


def get_provider(name: str):
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise Unsupported(f"Provider {name!r} is not supported in this MVP") from exc


def all_providers():
    return list(_PROVIDERS.values())
