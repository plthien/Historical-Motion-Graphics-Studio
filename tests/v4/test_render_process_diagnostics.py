from __future__ import annotations

import pytest

from app.api.errors import AppError
from app.modules.v4 import render_process_diagnostics as diagnostics


def _render_log_message(template: str, args: tuple[object, ...]) -> str:
    return template % args if args else template


def test_render_process_diagnostics_logs_nonzero_exit_with_sanitized_tail(monkeypatch) -> None:
    monkeypatch.setattr(
        diagnostics,
        "_ORIGINAL_RUN_OWNED_PROCESS",
        lambda *_args, **_kwargs: {
            "return_code": 7,
            "stderr_tail": [r"C:\controlled\input.png: filter failed"],
            "stdout_tail": ["progress=end"],
        },
    )
    logged: list[str] = []
    monkeypatch.setattr(
        diagnostics.logger,
        "error",
        lambda template, *args, **_kwargs: logged.append(_render_log_message(template, args)),
    )

    result = diagnostics._diagnostic_run_owned_process(
        ["ffmpeg", "-i", r"C:\controlled\input.png"],
        process_kind="final_encode",
    )

    assert result["return_code"] == 7
    rendered = "\n".join(logged)
    assert "process_kind=final_encode" in rendered
    assert "return_code=7" in rendered
    assert "<controlled-path>: filter failed" in rendered
    assert r"C:\controlled\input.png" not in rendered


def test_render_process_diagnostics_logs_original_app_error(monkeypatch) -> None:
    expected = AppError(
        "RENDER_TEST_ORIGINAL",
        "original callback failure",
        409,
        retryable=True,
        details={"phase": "progress_commit"},
    )

    def raise_original(*_args, **_kwargs):
        raise expected

    monkeypatch.setattr(diagnostics, "_ORIGINAL_RUN_OWNED_PROCESS", raise_original)
    logged: list[str] = []
    monkeypatch.setattr(
        diagnostics.logger,
        "exception",
        lambda template, *args, **_kwargs: logged.append(_render_log_message(template, args)),
    )

    with pytest.raises(AppError) as exc_info:
        diagnostics._diagnostic_run_owned_process(
            ["ffmpeg"],
            process_kind="final_encode",
        )

    assert exc_info.value is expected
    rendered = "\n".join(logged)
    assert "code=RENDER_TEST_ORIGINAL" in rendered
    assert "original callback failure" in rendered
    assert "progress_commit" in rendered
