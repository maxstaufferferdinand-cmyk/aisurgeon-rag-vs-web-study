"""Portable local configuration paths without repository-embedded user names."""

from __future__ import annotations

import os
from pathlib import Path


def secret_env_path() -> Path:
    """Return the external secret file path without reading or logging it.

    ``AISURGEON_SECRET_ENV_FILE`` takes precedence.  Otherwise the path follows
    the XDG config convention and therefore remains portable across accounts.
    """

    configured = os.environ.get("AISURGEON_SECRET_ENV_FILE")
    if configured:
        return Path(configured).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "aisurgeon-decentralised" / ".env"


__all__ = ["secret_env_path"]
