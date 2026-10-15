from __future__ import annotations

from typing import Any

__all__ = ["MediaTaskService"]


def __getattr__(name: str) -> Any:
    if name == "MediaTaskService":
        from app.modules.media.media_task_service import MediaTaskService

        return MediaTaskService
    raise AttributeError(name)
