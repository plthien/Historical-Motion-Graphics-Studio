from __future__ import annotations

import logging
import ntpath
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from app.modules.v4.render_filter_expression import normalize_padded_frame_select_file
from app.modules.v4.windows_job_object import WindowsJobObject


logger = logging.getLogger(__name__)
_ORIGINAL_POPEN = subprocess.Popen
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_PROCESSES: dict[int, "WindowsJobOwnedPopen"] = {}
_EXECUTION_BLOCKED = threading.Event()
_INSTALLED = False
WINDOWS_CREATE_PROCESS_COMMAND_LIMIT = 32_767
WINDOWS_RENDER_COMMAND_SAFETY_LIMIT = 24_000
_WINDOWS_MEDIA_EXECUTABLES = {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"}
_LEGACY_FILTER_SCRIPT_OPTION = "-filter_complex_script"
_CANONICAL_FILTER_SCRIPT_OPTION = "-/filter_complex"
_FILTER_SCRIPT_OPTIONS = {_LEGACY_FILTER_SCRIPT_OPTION, _CANONICAL_FILTER_SCRIPT_OPTION}


def _windows_command_line_chars(command: list[str]) -> int:
    return len(subprocess.list2cmdline(command))


def _canonicalize_ffmpeg_filter_script_option(command: list[str]) -> list[str]:
    """Use FFmpeg's supported file-backed complex-filter option.

    ``-filter_complex_script`` was deprecated upstream in favor of the generic
    ``-/filter_complex <file>`` file-loading syntax and is absent in FFmpeg 9.
    Canonicalizing at the Windows Render process boundary keeps older compiler
    output compatible with both FFmpeg 8.x and current FFmpeg releases without
    changing the filter graph bytes or their attempt-owned file path.
    """

    normalized = [os.fspath(item) for item in command]
    if not normalized or ntpath.basename(normalized[0]).lower() not in {"ffmpeg", "ffmpeg.exe"}:
        return normalized
    return [
        _CANONICAL_FILTER_SCRIPT_OPTION if token == _LEGACY_FILTER_SCRIPT_OPTION else token
        for token in normalized
    ]


def _normalize_ffmpeg_filter_script(command: list[str], *, cwd: str | os.PathLike[str] | None = None) -> int:
    """Bound generated padded-frame select depth before FFmpeg parses it.

    The production Render compiler externalizes filter graphs to an attempt-owned
    script. Large projects can contain more than FFmpeg's expression evaluator
    depth limit worth of padded-frame ``eq(n,index)`` terms. Normalize only the
    known generated post-concat expression, preserving the exact frame set and
    every other filter/timestamp operation.

    Windows command compaction can make the filter-script argv relative and set
    a process cwd. Resolve against that exact launch cwd so normalization cannot
    silently miss the file after compaction.
    """

    normalized = [os.fspath(item) for item in command]
    if not normalized or ntpath.basename(normalized[0]).lower() not in {"ffmpeg", "ffmpeg.exe"}:
        return 0
    launch_cwd = Path(cwd) if cwd not in {None, ""} else None
    for index, token in enumerate(normalized[:-1]):
        if token not in _FILTER_SCRIPT_OPTIONS:
            continue
        script_path = Path(normalized[index + 1])
        if not script_path.is_absolute() and launch_cwd is not None:
            script_path = launch_cwd / script_path
        if not script_path.is_file():
            continue
        try:
            result = normalize_padded_frame_select_file(script_path)
        except OSError as exc:
            raise OSError(f"Render filter script could not be normalized before FFmpeg launch: {script_path.name}") from exc
        if result.changed:
            logger.info(
                "Rebalanced FFmpeg padded-frame select expression terms=%s",
                result.padded_frame_terms,
            )
        return result.padded_frame_terms
    return 0


def _compact_windows_media_command(command: list[str]) -> tuple[list[str], str | None]:
    """Shorten long FFmpeg/FFprobe argv by making media paths cwd-relative.

    Windows CreateProcess has a hard command-line limit. Render graphs already
    externalize the filter graph, but a large project can still repeat a long
    absolute artifact-root prefix once for every ``-i`` argument. When that
    command approaches the Windows limit, keep the exact same files and argv
    semantics while replacing absolute media paths with paths relative to their
    deepest common parent. The caller then supplies that parent as ``cwd``.
    """

    normalized = [os.fspath(item) for item in command]
    if not normalized or _windows_command_line_chars(normalized) < WINDOWS_RENDER_COMMAND_SAFETY_LIMIT:
        return normalized, None
    if ntpath.basename(normalized[0]).lower() not in _WINDOWS_MEDIA_EXECUTABLES:
        return normalized, None

    absolute_paths: list[tuple[int, str]] = []
    for index, token in enumerate(normalized[1:], start=1):
        drive, tail = ntpath.splitdrive(token)
        if drive and tail.startswith(("\\", "/")):
            absolute_paths.append((index, ntpath.normpath(token)))
    if len(absolute_paths) < 2:
        return normalized, None

    try:
        common_parent = ntpath.commonpath(
            [ntpath.dirname(path) for _, path in absolute_paths]
        )
    except ValueError:
        return normalized, None
    common_drive, common_tail = ntpath.splitdrive(common_parent)
    if not common_drive or common_tail in {"", "\\", "/"}:
        return normalized, None

    compacted = list(normalized)
    for index, path in absolute_paths:
        compacted[index] = ntpath.relpath(path, common_parent)
    compacted_chars = _windows_command_line_chars(compacted)
    if compacted_chars >= _windows_command_line_chars(normalized):
        return normalized, None
    if compacted_chars >= WINDOWS_CREATE_PROCESS_COMMAND_LIMIT:
        return normalized, None
    return compacted, common_parent


class WindowsJobOwnedPopen(_ORIGINAL_POPEN):
    """Popen whose exact child process tree is owned by a Windows Job Object.

    The owning Job Object handle lives only in the durable Render host process.
    Normal completion closes the handle after the root process exits; host crash
    lets Windows close the handle automatically. Both paths therefore terminate
    any descendant process that outlives the root process.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if _EXECUTION_BLOCKED.is_set():
            raise OSError("Render child execution is blocked after durable ownership loss.")

        candidate = args[0] if args else kwargs.get("args")
        if isinstance(candidate, (list, tuple)):
            original_command = [os.fspath(item) for item in candidate]
            prepared_command = _canonicalize_ffmpeg_filter_script_option(original_command)
            launch_command = prepared_command
            process_cwd = os.fspath(kwargs["cwd"]) if kwargs.get("cwd") not in {None, ""} else None
            if process_cwd is None:
                launch_command, process_cwd = _compact_windows_media_command(prepared_command)
            _normalize_ffmpeg_filter_script(launch_command, cwd=process_cwd)
            if args:
                args = (launch_command, *args[1:])
            else:
                kwargs["args"] = launch_command
            if process_cwd is not None and kwargs.get("cwd") in {None, ""} and os.path.isdir(process_cwd):
                kwargs["cwd"] = process_cwd
                logger.info(
                    "Compacted Windows Render media command original_chars=%s compacted_chars=%s path_args=%s",
                    _windows_command_line_chars(original_command),
                    _windows_command_line_chars(launch_command),
                    sum(
                        1
                        for token in original_command[1:]
                        if ntpath.splitdrive(token)[0]
                        and ntpath.splitdrive(token)[1].startswith(("\\", "/"))
                    ),
                )

        super().__init__(*args, **kwargs)
        self._render_job_object: WindowsJobObject | None = None
        job: WindowsJobObject | None = None
        try:
            job = WindowsJobObject()
            job.assign_process(self)
            self._render_job_object = job
            with _ACTIVE_LOCK:
                _ACTIVE_PROCESSES[id(self)] = self
        except Exception:
            try:
                super().kill()
                super().wait(timeout=5)
            except Exception:
                pass
            if job is not None:
                job.close()
            self._render_job_object = None
            raise

    def _release_render_job(self) -> None:
        job = getattr(self, "_render_job_object", None)
        if job is None:
            return
        self._render_job_object = None
        with _ACTIVE_LOCK:
            _ACTIVE_PROCESSES.pop(id(self), None)
        job.close()

    def kill(self) -> None:
        job = getattr(self, "_render_job_object", None)
        if job is not None:
            try:
                job.terminate(exit_code=1)
                return
            except Exception:
                pass
        super().kill()

    def terminate(self) -> None:
        self.kill()

    def wait(self, timeout: float | None = None) -> int:
        return_code = super().wait(timeout=timeout)
        self._release_render_job()
        return return_code

    def __exit__(self, exc_type: Any, value: Any, traceback: Any) -> None:
        try:
            return super().__exit__(exc_type, value, traceback)
        finally:
            if self.returncode is not None:
                self._release_render_job()


def install_windows_subprocess_job_ownership() -> bool:
    """Install process-local Windows ownership for every Render-host child."""

    global _INSTALLED
    if os.name != "nt":
        return False
    if _INSTALLED:
        return True
    if subprocess.Popen is not _ORIGINAL_POPEN:
        raise RuntimeError("subprocess.Popen was replaced before Render process ownership setup.")
    subprocess.Popen = WindowsJobOwnedPopen  # type: ignore[assignment]
    _INSTALLED = True
    return True


def block_and_terminate_owned_processes() -> None:
    """Fence future child starts and terminate every currently owned process tree."""

    _EXECUTION_BLOCKED.set()
    with _ACTIVE_LOCK:
        processes = list(_ACTIVE_PROCESSES.values())
    for process in processes:
        try:
            process.kill()
        except Exception:
            # Commit fencing remains the SQLite safety boundary. This path is
            # best-effort only because the host is about to fail the operation.
            continue


def reset_guard_for_tests() -> None:
    """Reset only in-process guard state used by focused unit tests."""

    _EXECUTION_BLOCKED.clear()
