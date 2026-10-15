from __future__ import annotations

import ctypes
import ntpath
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.api.errors import AppError
from app.db.enums import JobStatus
from app.modules.v4.render_durable_recovery import reset_interrupted_render_attempt
from app.modules.v4.render_filter_expression import (
    normalize_padded_frame_select_file,
    normalize_padded_frame_select_script,
)
from app.modules.v4.windows_job_object import (
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    WindowsJobObject,
    _JobObjectExtendedLimitInformation,
)
from app.modules.v4.windows_subprocess_guard import (
    WINDOWS_CREATE_PROCESS_COMMAND_LIMIT,
    WINDOWS_RENDER_COMMAND_SAFETY_LIMIT,
    WindowsJobOwnedPopen,
    _canonicalize_ffmpeg_filter_script_option,
    _compact_windows_media_command,
    _normalize_ffmpeg_filter_script,
)
from app.render_coordinator_runner import (
    RenderCoordinatorRunner,
    RenderReconcileLease,
    _RenderLeaseKeepalive,
)


class _FakeKernel32:
    def __init__(self) -> None:
        self.limit_flags = 0
        self.assigned: list[tuple[int, int]] = []
        self.terminated: list[tuple[int, int]] = []
        self.closed: list[int] = []

    def CreateJobObjectW(self, _security, _name):
        return 101

    def SetInformationJobObject(self, handle, info_class, info_ptr, _info_size):
        assert handle == 101
        assert info_class == 9
        info = ctypes.cast(
            info_ptr,
            ctypes.POINTER(_JobObjectExtendedLimitInformation),
        ).contents
        self.limit_flags = int(info.BasicLimitInformation.LimitFlags)
        return 1

    def AssignProcessToJobObject(self, job_handle, process_handle):
        self.assigned.append((int(job_handle), int(process_handle.value)))
        return 1

    def TerminateJobObject(self, job_handle, exit_code):
        self.terminated.append((int(job_handle), int(exit_code)))
        return 1

    def CloseHandle(self, handle):
        self.closed.append(int(handle))
        return 1

    def GetCurrentProcess(self):
        return 202


class _FakeJob:
    def __init__(self) -> None:
        self.exit_codes: list[int] = []

    def terminate(self, *, exit_code: int = 1) -> None:
        self.exit_codes.append(exit_code)


def _interrupted_state(*, cancellation_requested: bool = False):
    operation = SimpleNamespace(
        render_operation_id="render_operation_1",
        status="rendering",
        attempt_no=2,
        attempt_token="attempt-old",
        attempt_relative_output_path="project/render/attempt-2/final_video.mp4.part",
        process_pid=4321,
        process_started_at="2026-09-07T00:00:00+00:00",
        worker_instance_id="pid-999",
        recovery_enqueued=False,
        cancellation_requested=cancellation_requested,
        progress_time_units=50,
        progress_frame=30,
        progress_percent=40,
        request_json={"active_process": {"process_kind": "final_encode"}},
        error_code="old",
        error_message="old",
        error_details_json={"old": True},
        completed_at=None,
        updated_at="2026-09-07T00:00:00+00:00",
        next_reconcile_at="2026-09-07T00:00:00+00:00",
        state_revision=4,
    )
    job = SimpleNamespace(
        status=JobStatus.RUNNING,
        progress=40,
        attempt=2,
        retryable=False,
        error_code=None,
        error_message=None,
        error_details_json=None,
        started_at="2026-09-07T00:00:00+00:00",
        finished_at=None,
        completed_at=None,
        heartbeat_at="2026-09-07T00:00:00+00:00",
        updated_at="2026-09-07T00:00:00+00:00",
    )
    return operation, job


def _large_select_script(*, term_count: int = 160, frame_rate: int = 30) -> str:
    drop_expression = "+".join(f"eq(n,{index})" for index in range(term_count))
    return (
        "[0:v]null[vconcat];\n"
        f"[vconcat]select='not({drop_expression})',settb=AVTB,setpts=N/({frame_rate}*TB)[vout];\n"
    )


def _max_parenthesis_depth(value: str) -> int:
    depth = 0
    maximum = 0
    for character in value:
        if character == "(":
            depth += 1
            maximum = max(maximum, depth)
        elif character == ")":
            depth -= 1
    assert depth == 0
    return maximum


def test_windows_job_object_owns_process_with_kill_on_close() -> None:
    kernel32 = _FakeKernel32()
    job = WindowsJobObject(kernel32=kernel32)

    assert kernel32.limit_flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    job.assign_process_handle(202)
    job.terminate(exit_code=9)
    job.close()

    assert kernel32.assigned == [(101, 202)]
    assert kernel32.terminated == [(101, 9)]
    assert kernel32.closed == [101]


def test_windows_owned_process_cancel_and_timeout_escalation_kill_job_tree() -> None:
    process = object.__new__(WindowsJobOwnedPopen)
    process._child_created = False
    process.returncode = None
    fake_job = _FakeJob()
    process._render_job_object = fake_job  # type: ignore[assignment]

    process.terminate()
    process.kill()

    assert fake_job.exit_codes == [1, 1]


def test_windows_guard_canonicalizes_removed_ffmpeg_filter_script_option() -> None:
    command = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "-hide_banner",
        "-filter_complex_script",
        r"C:\yas3\render\attempt\filter_complex.txt",
        "-f",
        "null",
        "-",
    ]

    prepared = _canonicalize_ffmpeg_filter_script_option(command)

    assert prepared[0] == command[0]
    assert "-filter_complex_script" not in prepared
    assert prepared[2] == "-/filter_complex"
    assert prepared[3:] == command[3:]


def test_long_ffmpeg_command_compacts_absolute_media_paths_below_windows_limit() -> None:
    root = ntpath.normpath(
        r"C:\yas3\youtube_ai_studio_release_candidate\youtube_ai_studio_release_candidate"
        r"\youtube_ai_studio_release_candidate\youtube_ai_studio_release_candidate"
        r"\artifacts\project\project_1234567890"
    )
    command = [r"C:\ffmpeg\bin\ffmpeg.exe", "-hide_banner", "-nostdin", "-y"]
    for index in range(180):
        command += ["-loop", "1", "-i", ntpath.join(root, "inputs", f"artifact_{index:03d}.png")]
    command += [
        "-/filter_complex",
        ntpath.join(root, "attempt", "filter_complex.txt"),
        "-f",
        "mp4",
        ntpath.join(root, "attempt", "final_video.mp4.part"),
    ]

    original_chars = len(subprocess.list2cmdline(command))
    assert original_chars >= WINDOWS_CREATE_PROCESS_COMMAND_LIMIT

    compacted, process_cwd = _compact_windows_media_command(command)

    assert process_cwd == root
    assert len(subprocess.list2cmdline(compacted)) < WINDOWS_RENDER_COMMAND_SAFETY_LIMIT
    assert root not in "\n".join(compacted[1:])
    assert ntpath.join("inputs", "artifact_000.png") in compacted
    assert ntpath.join("attempt", "filter_complex.txt") in compacted
    assert ntpath.join("attempt", "final_video.mp4.part") in compacted


def test_short_ffmpeg_command_keeps_absolute_paths_and_no_cwd() -> None:
    command = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "-i",
        r"C:\yas3\artifacts\input.png",
        r"C:\yas3\artifacts\output.mp4",
    ]

    compacted, process_cwd = _compact_windows_media_command(command)

    assert compacted == command
    assert process_cwd is None


def test_long_non_media_command_is_not_rewritten() -> None:
    root = r"C:\yas3\artifacts\project\project_1"
    command = [r"C:\Python312\python.exe"]
    for index in range(220):
        command += ["--input", ntpath.join(root, f"artifact_{index:03d}.json")]
    while len(subprocess.list2cmdline(command)) < WINDOWS_RENDER_COMMAND_SAFETY_LIMIT:
        command += ["--input", ntpath.join(root, f"artifact_extra_{len(command):04d}.json")]

    compacted, process_cwd = _compact_windows_media_command(command)

    assert compacted == command
    assert process_cwd is None


def test_large_padded_frame_select_is_balanced_without_changing_drop_terms() -> None:
    original = _large_select_script(term_count=160)

    result = normalize_padded_frame_select_script(original)

    assert result.changed
    assert result.padded_frame_terms == 160
    assert _max_parenthesis_depth(result.script) < 20
    for index in range(160):
        assert result.script.count(f"eq(n,{index})") == 1
    assert "tpad" not in original  # This fixture isolates only the post-concat predicate compiler.


def test_windows_guard_normalizes_attempt_filter_before_ffmpeg_launch(tmp_path) -> None:
    filter_path = tmp_path / "filter_complex.txt"
    filter_path.write_text(_large_select_script(term_count=160), encoding="utf-8", newline="\n")

    term_count = _normalize_ffmpeg_filter_script(
        ["ffmpeg.exe", "-/filter_complex", str(filter_path), "-f", "null", "-"]
    )

    assert term_count == 160
    normalized = filter_path.read_text(encoding="utf-8")
    assert _max_parenthesis_depth(normalized) < 20


def test_windows_guard_normalizes_relative_filter_after_command_compaction(tmp_path) -> None:
    filter_path = tmp_path / "filter_complex.txt"
    filter_path.write_text(_large_select_script(term_count=160), encoding="utf-8", newline="\n")

    term_count = _normalize_ffmpeg_filter_script(
        ["ffmpeg.exe", "-/filter_complex", "filter_complex.txt", "-f", "null", "-"],
        cwd=tmp_path,
    )

    assert term_count == 160
    normalized = filter_path.read_text(encoding="utf-8")
    assert _max_parenthesis_depth(normalized) < 20


def test_real_ffmpeg_accepts_large_balanced_padded_frame_select(tmp_path) -> None:
    if os.name != "nt":
        pytest.skip("This executable compatibility acceptance belongs to the Windows Render gate.")
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the Render filter-expression acceptance test.")

    filter_path = tmp_path / "filter_complex.txt"
    filter_path.write_text(_large_select_script(term_count=160), encoding="utf-8", newline="\n")
    result = normalize_padded_frame_select_file(filter_path)
    assert result.changed
    assert result.padded_frame_terms == 160

    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=16x16:rate=30:duration=7",
            "-/filter_complex",
            str(filter_path),
            "-map",
            "[vout]",
            "-progress",
            "pipe:1",
            "-nostats",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    final_frames = [line for line in completed.stdout.splitlines() if line.startswith("frame=")]
    assert final_frames
    assert final_frames[-1] == "frame=50"


def test_lease_loss_invokes_process_tree_termination_hook() -> None:
    terminated: list[str] = []
    runner = RenderCoordinatorRunner(
        lambda: None,
        fatal_execution_loss=lambda operation_id: terminated.append(operation_id),
    )
    lease = RenderReconcileLease(
        operation_id="render_operation_1",
        owner="host-a",
        token="token-a",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    keepalive = _RenderLeaseKeepalive(runner, lease)

    keepalive._mark_lost("test")

    assert terminated == ["render_operation_1"]
    with pytest.raises(AppError) as exc_info:
        keepalive.assert_not_lost()
    assert exc_info.value.error_code == "RENDER_EXECUTION_FENCE_LOST"


def test_crashed_durable_attempt_recovers_as_new_attempt() -> None:
    operation, job = _interrupted_state()
    cleaned: list[str] = []

    should_execute = reset_interrupted_render_attempt(
        operation,
        job,
        now_iso="2026-09-08T00:00:00+00:00",
        cleanup_previous_attempt=lambda current: cleaned.append(current.attempt_token),
    )

    assert should_execute
    assert cleaned == ["attempt-old"]
    assert operation.status == "queued"
    assert operation.attempt_no == 3
    assert operation.attempt_token is None
    assert operation.attempt_relative_output_path is None
    assert operation.process_pid is None
    assert operation.progress_percent == 0
    assert "active_process" not in operation.request_json
    assert job.status == JobStatus.QUEUED
    assert job.attempt == 3


def test_crashed_attempt_with_persisted_cancel_does_not_restart() -> None:
    operation, job = _interrupted_state(cancellation_requested=True)

    should_execute = reset_interrupted_render_attempt(
        operation,
        job,
        now_iso="2026-09-08T00:00:00+00:00",
        cleanup_previous_attempt=lambda _current: None,
    )

    assert not should_execute
    assert operation.status == "cancelled"
    assert operation.attempt_no == 2
    assert job.status == JobStatus.CANCELLED
    assert job.attempt == 2
