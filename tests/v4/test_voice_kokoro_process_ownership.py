from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
import wave

import pytest

from app.api.errors import AppError
from app.core.config import Settings
from app.modules.v4.host_owned_process import HostOwnedProcess
from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from app.modules.v4.voice_provider_contracts import VoiceProviderPacingDirective, VoiceSynthesisRequest


class _FakeProcess:
    def __init__(
        self,
        *,
        polls: list[int | None] | None = None,
        returncode: int | None = 0,
        on_wait=None,
    ) -> None:
        self.pid = 1234
        self._handle = 4321
        self._polls = list(polls or [returncode])
        self.returncode = returncode
        self._on_wait = on_wait
        self.terminated = False
        self.killed = False
        self.waits = 0

    def poll(self) -> int | None:
        if self._polls:
            value = self._polls.pop(0)
            self.returncode = value
            return value
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.waits += 1
        if self._on_wait is not None:
            self._on_wait()
            self._on_wait = None
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _FakeJob:
    def __init__(self) -> None:
        self.assigned_handle: int | None = None
        self.terminated = False
        self.closed = False

    def assign_process(self, process: _FakeProcess) -> None:
        self.assigned_handle = process._handle

    def terminate(self, *, exit_code: int = 1) -> None:
        _ = exit_code
        self.terminated = True

    def close(self) -> None:
        self.closed = True


class _Control:
    def __init__(self, *, cancel: bool = False, deadline: bool = False) -> None:
        self.cancel = cancel
        self.deadline = deadline

    @property
    def execution_attempt_token(self) -> str:
        return "attempt_001"

    def cancellation_requested(self) -> bool:
        return self.cancel

    def deadline_reached(self) -> bool:
        return self.deadline


class _ExplodingControl(_Control):
    def cancellation_requested(self) -> bool:
        raise RuntimeError("controlled unexpected failure")


def _request(tmp_path: Path) -> VoiceSynthesisRequest:
    return VoiceSynthesisRequest(
        operation_id="voice_operation_001",
        segment_task_id="voice_segment_task_001",
        execution_attempt_token="attempt_001",
        idempotency_key="idem_001",
        exact_text="hello",
        voice_id="af_heart",
        language="en-us",
        speaking_rate=1.0,
        requested_sample_rate_hz=48_000,
        requested_output_format="wav_pcm_s16le",
        provider_options={},
        attempt_workspace=tmp_path,
        pacing=VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=1.0,
            target_narration_wpm=160,
            policy_version="kokoro_relative_multiplier_v1",
        ),
    )


def _write_part_wav(path: Path, value: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        wav.writeframes(int(value).to_bytes(2, "little", signed=True) * 2)


def test_host_owned_process_assigns_and_releases_job_object(tmp_path: Path) -> None:
    process = _FakeProcess()
    jobs: list[_FakeJob] = []

    owned = HostOwnedProcess.start(
        ["kokoro"],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        env={},
        popen_factory=lambda *args, **kwargs: process,
        job_factory=lambda: jobs.append(_FakeJob()) or jobs[-1],
        use_job_object=True,
    )

    assert owned.owns_process_tree is True
    assert jobs[0].assigned_handle == process._handle
    owned.release()
    assert jobs[0].closed is True
    assert process.killed is False


def test_host_owned_process_terminates_job_tree_before_release(tmp_path: Path) -> None:
    process = _FakeProcess(polls=[None], returncode=None)
    jobs: list[_FakeJob] = []
    owned = HostOwnedProcess.start(
        ["kokoro"],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        env={},
        popen_factory=lambda *args, **kwargs: process,
        job_factory=lambda: jobs.append(_FakeJob()) or jobs[-1],
        use_job_object=True,
    )

    owned.terminate_tree(exit_code=1)
    owned.release()

    assert jobs[0].terminated is True
    assert jobs[0].closed is True


def test_kokoro_run_process_releases_host_owned_process_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = KokoroProviderAdapter(Settings(root_dir=tmp_path, kokoro_base_path=".", kokoro_command="kokoro"))
    owned = HostOwnedProcess(_FakeProcess(polls=[None, 0], returncode=0))
    monkeypatch.setattr(adapter, "_start_owned_process", lambda *args, **kwargs: owned)
    monkeypatch.setattr("app.modules.v4.voice_provider_adapters.time.sleep", lambda _seconds: None)

    process = adapter._run_process(
        request=_request(tmp_path),
        control=_Control(),
        root=tmp_path,
        command=["kokoro"],
        key=("voice_segment_task_001", "attempt_001"),
        stderr_capture=subprocess.DEVNULL,
    )

    assert process is owned
    assert owned.released is True
    assert adapter._active_processes == {}
    assert adapter.reconcile_submission(_request(tmp_path), None, _Control()).status == "replay_safe"


def test_kokoro_run_process_releases_job_object_on_provider_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = KokoroProviderAdapter(Settings(root_dir=tmp_path, kokoro_base_path=".", kokoro_command="kokoro"))
    job = _FakeJob()
    owned = HostOwnedProcess(_FakeProcess(polls=[1], returncode=1), job=job)
    monkeypatch.setattr(adapter, "_start_owned_process", lambda *args, **kwargs: owned)

    with tempfile.TemporaryFile(mode="w+b") as stderr_capture:
        with pytest.raises(AppError) as exc_info:
            adapter._run_process(
                request=_request(tmp_path),
                control=_Control(),
                root=tmp_path,
                command=["kokoro"],
                key=("voice_segment_task_001", "attempt_001"),
                stderr_capture=stderr_capture,
            )

    assert exc_info.value.error_code == "TTS_PROVIDER_FAILED"
    assert owned.released is True
    assert job.closed is True
    assert job.terminated is False
    assert adapter._active_processes == {}


def test_kokoro_run_process_terminates_active_child_on_unexpected_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = KokoroProviderAdapter(Settings(root_dir=tmp_path, kokoro_base_path=".", kokoro_command="kokoro"))
    job = _FakeJob()
    fake_process = _FakeProcess(polls=[None], returncode=None)
    owned = HostOwnedProcess(fake_process, job=job)
    monkeypatch.setattr(adapter, "_start_owned_process", lambda *args, **kwargs: owned)

    with tempfile.TemporaryFile(mode="w+b") as stderr_capture:
        with pytest.raises(RuntimeError, match="controlled unexpected failure"):
            adapter._run_process(
                request=_request(tmp_path),
                control=_ExplodingControl(),
                root=tmp_path,
                command=["kokoro"],
                key=("voice_segment_task_001", "attempt_001"),
                stderr_capture=stderr_capture,
            )

    assert job.terminated is True
    assert job.closed is True
    assert owned.released is True
    assert adapter._active_processes == {}


@pytest.mark.parametrize(
    ("control", "error_code"),
    [(_Control(cancel=True), "VOICE_OPERATION_CANCELLED"), (_Control(deadline=True), "TTS_PROVIDER_TIMEOUT")],
)
def test_kokoro_run_process_terminates_owned_process_on_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: _Control,
    error_code: str,
) -> None:
    adapter = KokoroProviderAdapter(Settings(root_dir=tmp_path, kokoro_base_path=".", kokoro_command="kokoro"))
    fake_process = _FakeProcess(polls=[None], returncode=None)
    owned = HostOwnedProcess(fake_process)
    monkeypatch.setattr(adapter, "_start_owned_process", lambda *args, **kwargs: owned)
    monkeypatch.setattr("app.modules.v4.voice_provider_adapters.time.sleep", lambda _seconds: None)

    with pytest.raises(AppError) as exc_info:
        adapter._run_process(
            request=_request(tmp_path),
            control=control,
            root=tmp_path,
            command=["kokoro"],
            key=("voice_segment_task_001", "attempt_001"),
            stderr_capture=subprocess.DEVNULL,
        )

    assert exc_info.value.error_code == error_code
    assert fake_process.terminated is True
    assert owned.released is True
    assert adapter._active_processes == {}


def test_kokoro_piecewise_provider_failure_releases_each_process_and_cleans_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = KokoroProviderAdapter(Settings(root_dir=tmp_path, kokoro_base_path=".", kokoro_command="kokoro"))
    jobs: list[_FakeJob] = []

    def fake_start(command, **_kwargs):
        output_path = Path(command[command.index("--output-file") + 1])
        job = _FakeJob()
        jobs.append(job)
        if len(jobs) == 1:
            process = _FakeProcess(
                polls=[0],
                returncode=0,
                on_wait=lambda: _write_part_wav(output_path, 1),
            )
        else:
            process = _FakeProcess(polls=[1], returncode=1)
        return HostOwnedProcess(process, job=job)

    monkeypatch.setattr(adapter, "_start_owned_process", fake_start)
    monkeypatch.setattr(
        adapter,
        "_build_command",
        lambda request, input_path, output_path: [
            "kokoro",
            "--input-file", str(input_path),
            "--output-file", str(output_path),
        ],
    )
    output_path = tmp_path / "voice.wav"
    request = _request(tmp_path)

    with pytest.raises(AppError) as exc_info:
        adapter._synthesize_piece_plan(
            request=request,
            control=_Control(),
            root=tmp_path,
            output_path=output_path,
            pieces=[
                {"kind": "text", "text": "hello"},
                {"kind": "text", "text": "world"},
            ],
            key=("voice_segment_task_001", "attempt_001"),
        )

    assert exc_info.value.error_code == "TTS_PROVIDER_FAILED"
    assert len(jobs) == 2
    assert all(job.closed for job in jobs)
    assert adapter._active_processes == {}
    assert not output_path.exists()
    assert not output_path.with_suffix(".part0.txt").exists()
    assert not output_path.with_suffix(".part0.wav").exists()
    assert not output_path.with_suffix(".part1.txt").exists()
    assert not output_path.with_suffix(".part1.wav").exists()
