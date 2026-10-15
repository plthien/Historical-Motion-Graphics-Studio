from __future__ import annotations

import logging
from typing import Any

from app.api.errors import AppError
from app.modules.v4 import render_ffmpeg


logger = logging.getLogger(__name__)
_ORIGINAL_RUN_OWNED_PROCESS = render_ffmpeg.run_owned_process
_INSTALLED = False


def _command_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[str]:
    candidate = args[0] if args else kwargs.get("args")
    if not isinstance(candidate, list):
        return []
    return [str(item) for item in candidate]


def _sanitize_tail(lines: Any, command: list[str]) -> list[str]:
    if not isinstance(lines, list):
        return []
    path_tokens = sorted(
        {
            item
            for item in command
            if item.startswith("/") or (len(item) > 2 and item[1:3] == ":\\")
        },
        key=len,
        reverse=True,
    )
    sanitized: list[str] = []
    for raw in lines[-20:]:
        safe = str(raw)
        for token in path_tokens:
            safe = safe.replace(token, "<controlled-path>")
        sanitized.append(safe)
    return sanitized


def _diagnostic_run_owned_process(*args: Any, **kwargs: Any) -> dict[str, Any]:
    command = _command_from_call(args, kwargs)
    process_kind = str(kwargs.get("process_kind", "unknown"))
    try:
        result = _ORIGINAL_RUN_OWNED_PROCESS(*args, **kwargs)
    except AppError as exc:
        logger.exception(
            "Render owned process raised AppError process_kind=%s code=%s message=%s details=%s",
            process_kind,
            exc.error_code,
            exc.message,
            exc.details,
        )
        raise
    except Exception as exc:
        logger.exception(
            "Render owned process raised exception process_kind=%s exception_type=%s message=%s",
            process_kind,
            type(exc).__name__,
            str(exc),
        )
        raise

    return_code = result.get("return_code")
    if return_code not in (None, 0):
        logger.error(
            "Render owned process exited non-zero process_kind=%s return_code=%s stderr_tail=%s stdout_tail=%s",
            process_kind,
            return_code,
            _sanitize_tail(result.get("stderr_tail"), command),
            _sanitize_tail(result.get("stdout_tail"), command),
        )
    return result


def install_render_process_diagnostics() -> bool:
    """Install process-local diagnostic logging without changing Render behavior."""

    global _INSTALLED
    if _INSTALLED:
        return True
    if render_ffmpeg.run_owned_process is not _ORIGINAL_RUN_OWNED_PROCESS:
        raise RuntimeError("Render process runner was replaced before diagnostics setup.")
    render_ffmpeg.run_owned_process = _diagnostic_run_owned_process
    _INSTALLED = True
    return True
