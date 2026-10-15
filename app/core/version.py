from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version


def resolve_app_version() -> str:
    configured = os.getenv("APP_VERSION", "").strip()
    if configured:
        return configured
    try:
        return version("youtube-ai-studio")
    except PackageNotFoundError:
        return "3.0.0-external-authoring.18-rc1"


APP_VERSION = resolve_app_version()
