from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from kokoro.runtime_response_calibration import KOKORO_FLOW_VERSION

from app.api.errors import AppError
from app.core.config import Settings
from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderPacingDirective,
    VoiceProviderProfile,
    VoiceProviderVoice,
    VoiceSynthesisRequest,
)
from tests.support.kokoro_runtime_response import runtime_response_measurement


class _Control:
    @property
    def execution_attempt_token(self) -> str:
        return "attempt_001"

    def cancellation_requested(self) -> bool:
        return False

    def deadline_reached(self) -> bool:
        return False


def _write_part_wav(path: Path, value: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        wav.writeframes(int(value).to_bytes(2, "little", signed=True) * 2)


def test_kokoro_adapter_renders_delivery_pieces_with_deterministic_silence(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(
        root_dir=tmp_path,
        kokoro_base_path="kokoro",
        kokoro_command="python kokoro_cli.py",
        tts_provider_max_output_bytes=1_000_000,
    )
    adapter = KokoroProviderAdapter(settings)
    monkeypatch.setattr(
        adapter,
        "_ready_runtime_command",
        lambda: (tmp_path, ["python", "kokoro_cli.py"], tmp_path / "python", tmp_path / "kokoro_cli.py"),
    )
    monkeypatch.setattr(adapter, "_resolve_command_for_spawn", lambda command, root: command)
    monkeypatch.setattr(
        adapter,
        "list_voices",
        lambda profile=None: (VoiceProviderVoice(voice_id="af_heart", display_name="AF Heart", language="en-us"),),
    )

    calls: list[dict[str, str]] = []

    def fake_run_process(**kwargs):
        command = kwargs["command"]
        output_path = Path(command[command.index("--output-file") + 1])
        input_path = Path(command[command.index("--input-file") + 1])
        calls.append({
            "text": input_path.read_text(encoding="utf-8"),
            "language": command[command.index("--language") + 1],
            "speed": command[command.index("--speaking-rate") + 1],
        })
        _write_part_wav(output_path, len(calls))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(adapter, "_run_process", fake_run_process)

    request = VoiceSynthesisRequest(
        operation_id="voice_operation_001",
        segment_task_id="voice_segment_task_001",
        execution_attempt_token="attempt_001",
        idempotency_key="idem_001",
        exact_text="hello world",
        voice_id="af_heart",
        language="en-gb",
        speaking_rate=1.0,
        requested_sample_rate_hz=48_000,
        requested_output_format="wav_pcm_s16le",
        provider_options={
            "_kokoro_segment_payloads": {
                "voice_segment_task_001": {
                    "pieces": [
                        {"kind": "text", "text": "hello"},
                        {"kind": "silence", "duration_seconds": 0.01},
                        {"kind": "text", "text": "world"},
                    ]
                }
            }
        },
        attempt_workspace=tmp_path / "attempt",
        pacing=VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=1.2,
            target_narration_wpm=160,
            policy_version="kokoro_relative_multiplier_v1",
            value_range=(0.5, 2.0),
        ),
    )

    submission = adapter.submit(request, _Control())

    assert submission.status == "completed"
    assert calls == [
        {"text": "hello", "language": "en-gb", "speed": "1.2"},
        {"text": "world", "language": "en-gb", "speed": "1.2"},
    ]
    with wave.open(str(submission.result.audio_file_path), "rb") as wav:
        assert wav.getframerate() == 48_000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        frames = wav.readframes(wav.getnframes())
    assert len(frames) == (2 + 480 + 2) * 2
    assert frames[:4] == b"\x01\x00\x01\x00"
    assert frames[4:-4] == b"\x00" * 480 * 2
    assert frames[-4:] == b"\x02\x00\x02\x00"


_RUNTIME_PACKAGES = {
    "kokoro-onnx": "0.6.1",
    "onnxruntime": "1.29.0",
    "numpy": "2.0.2",
    "transitive-runtime-dependency": "4.5.6",
}


def _write_fake_runtime_packages(root: Path) -> None:
    site_packages = root / ".venv" / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
    site_packages.mkdir(parents=True, exist_ok=True)
    for name, version in _RUNTIME_PACKAGES.items():
        metadata = site_packages / f"{name.replace('-', '_')}-{version}.dist-info" / "METADATA"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")


def _prepare_runtime(
    tmp_path: Path,
    monkeypatch,
    *,
    marker: dict[str, object],
    command_variant: str = "exact",
) -> tuple[KokoroProviderAdapter, VoiceProviderProfile, Path, Path]:
    root = tmp_path / "kokoro"
    expected_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    expected_python.parent.mkdir(parents=True)
    expected_python.write_bytes(b"python")
    expected_python.chmod(0o755)
    _write_fake_runtime_packages(root)
    expected_script = root / "kokoro_cli.py"
    expected_script.write_text("# test wrapper\n", encoding="utf-8")
    response_source = root / "runtime_response_calibration.py"
    response_source.write_text("# test response model\n", encoding="utf-8")
    requirements = root / "requirements.txt"
    model = root / "kokoro-v1.0.onnx"
    voices = root / "voices-v1.0.bin"
    requirements.write_text("test\n", encoding="utf-8")
    model.write_bytes(b"model")
    voices.write_bytes(b"voices")
    marker = {
        "flow_version": KOKORO_FLOW_VERSION,
        "python_version": "3.12.0",
        "python_executable_sha256": hashlib.sha256(expected_python.read_bytes()).hexdigest(),
        "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
        "wrapper_source_sha256": hashlib.sha256(expected_script.read_bytes()).hexdigest(),
        "runtime_response_calibration_source_sha256": hashlib.sha256(response_source.read_bytes()).hexdigest(),
        "runtime_packages": dict(_RUNTIME_PACKAGES),
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "voices_sha256": hashlib.sha256(voices.read_bytes()).hexdigest(),
        "wrapper_contract": "kokoro_command_v3",
        "canonical_sample_rate_hz": 48_000,
        "output_format": "wav_pcm_s16le",
        **marker,
    }
    (root / ".yas3_kokoro_ready.json").write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_MODEL_SHA256", marker["model_sha256"])
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_VOICES_SHA256", marker["voices_sha256"])

    configured_python = expected_python
    configured_script = expected_script
    if command_variant == "foreign_executable":
        configured_python = tmp_path / "foreign" / expected_python.name
        configured_python.parent.mkdir(parents=True)
        configured_python.write_bytes(b"other-python")
        configured_python.chmod(0o755)
    elif command_variant == "foreign_script":
        configured_script = root / "other_cli.py"
        configured_script.write_text("# different entry\n", encoding="utf-8")
    elif command_variant not in {"exact", "extra_wrapper_arg", "extra_interpreter_arg", "bare_executable"}:
        raise AssertionError(f"Unknown command variant: {command_variant}")

    configured_command = f'"{configured_python}" "{configured_script}"'
    if command_variant == "extra_wrapper_arg":
        configured_command += " --max-chunk-characters 64"
    elif command_variant == "extra_interpreter_arg":
        configured_command = f'"{configured_python}" -X dev "{configured_script}"'
    elif command_variant == "bare_executable":
        configured_command = f'"{configured_python.name}" "{configured_script}"'
    settings = Settings(
        root_dir=tmp_path,
        kokoro_base_path="kokoro",
        kokoro_command=configured_command,
    )
    adapter = KokoroProviderAdapter(settings)
    monkeypatch.setattr(
        adapter,
        "list_voices",
        lambda profile=None: (VoiceProviderVoice(voice_id="af_heart", display_name="AF Heart", language="en-us"),),
    )
    profile = VoiceProviderProfile(
        profile_id="kokoro_local",
        provider_key="kokoro_local",
        enabled=True,
        display_name="Kokoro Local",
        voice_id="af_heart",
        language="en-us",
        speaking_rate=1.0,
        requested_sample_rate_hz=48_000,
        requested_output_format="wav_pcm_s16le",
        provider_options={"runtime": "local_kokoro_cli"},
        maximum_segment_characters=800,
    )
    return adapter, profile, root, expected_python

@pytest.mark.parametrize(
    ("marker_patch", "ready"),
    [
        ({"flow_version": "not-current", "runtime_response_calibration": runtime_response_measurement(0.72)}, False),
        ({"wrapper_contract": "kokoro_command_v2", "runtime_response_calibration": runtime_response_measurement(0.72)}, False),
        ({}, False),
        ({"runtime_response_calibration": {**runtime_response_measurement(0.72), "speed_response_exponent": 9.0}}, False),
        ({"runtime_response_calibration": runtime_response_measurement(0.72)}, True),
    ],
)
def test_kokoro_readiness_requires_current_marker_with_valid_runtime_calibration(tmp_path: Path, monkeypatch, marker_patch: dict[str, object], ready: bool) -> None:
    adapter, profile, _root, _expected_python = _prepare_runtime(tmp_path, monkeypatch, marker=marker_patch)

    result = adapter.readiness(profile)

    assert result.ready is ready
    if not ready:
        assert result.issue_code == "TTS_KOKORO_SETUP_INCOMPLETE"


@pytest.mark.parametrize(
    ("command_variant", "ready"),
    [
        ("exact", True),
        ("foreign_executable", False),
        ("foreign_script", False),
        ("extra_wrapper_arg", False),
        ("extra_interpreter_arg", False),
        ("bare_executable", False),
    ],
)
def test_kokoro_readiness_requires_setup_owned_runtime_command(
    tmp_path: Path,
    monkeypatch,
    command_variant: str,
    ready: bool,
) -> None:
    adapter, profile, _root, _expected_python = _prepare_runtime(
        tmp_path,
        monkeypatch,
        marker={"runtime_response_calibration": runtime_response_measurement(0.72)},
        command_variant=command_variant,
    )

    result = adapter.readiness(profile)

    assert result.ready is ready
    if not ready:
        assert result.issue_code == "TTS_KOKORO_SETUP_INCOMPLETE"


def test_kokoro_readiness_rejects_transitive_runtime_package_drift(tmp_path: Path, monkeypatch) -> None:
    adapter, profile, root, _expected_python = _prepare_runtime(
        tmp_path,
        monkeypatch,
        marker={"runtime_response_calibration": runtime_response_measurement(0.72)},
    )
    metadata = next((root / ".venv").glob("**/transitive_runtime_dependency-*.dist-info/METADATA"))
    metadata.write_text("Name: transitive-runtime-dependency\nVersion: 4.5.7\n", encoding="utf-8")

    result = adapter.readiness(profile)

    assert result.ready is False
    assert result.issue_code == "TTS_KOKORO_SETUP_INCOMPLETE"


def test_kokoro_readiness_rejects_python_executable_drift(tmp_path: Path, monkeypatch) -> None:
    adapter, profile, _root, expected_python = _prepare_runtime(
        tmp_path,
        monkeypatch,
        marker={"runtime_response_calibration": runtime_response_measurement(0.72)},
    )
    expected_python.write_bytes(b"changed-python")

    result = adapter.readiness(profile)

    assert result.ready is False
    assert result.issue_code == "TTS_KOKORO_SETUP_INCOMPLETE"



@pytest.mark.parametrize("relative_path", ["kokoro_cli.py", "runtime_response_calibration.py"])
def test_kokoro_readiness_rejects_calibrated_source_drift(tmp_path: Path, monkeypatch, relative_path: str) -> None:
    adapter, profile, root, _expected_python = _prepare_runtime(
        tmp_path,
        monkeypatch,
        marker={"runtime_response_calibration": runtime_response_measurement(0.72)},
    )
    source = root / relative_path
    source.write_text(source.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")

    result = adapter.readiness(profile)

    assert result.ready is False
    assert result.issue_code == "TTS_KOKORO_SETUP_INCOMPLETE"

def test_kokoro_runtime_subprocess_environment_ignores_python_import_overrides(tmp_path: Path, monkeypatch) -> None:
    adapter, _profile, _root, _expected_python = _prepare_runtime(
        tmp_path,
        monkeypatch,
        marker={"runtime_response_calibration": runtime_response_measurement(0.72)},
    )
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        monkeypatch.setenv(key, "foreign")
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)

    env = adapter._runtime_subprocess_env()

    assert all(key not in env for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"))
    assert env["PYTHONNOUSERSITE"] == "1"


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv symlink provenance case")
def test_kokoro_readiness_rejects_direct_symlink_target_interpreter(tmp_path: Path, monkeypatch) -> None:
    adapter, profile, root, expected_python = _prepare_runtime(
        tmp_path,
        monkeypatch,
        marker={"runtime_response_calibration": runtime_response_measurement(0.72)},
    )
    _ = adapter
    expected_python.unlink()
    base_python = tmp_path / "base-python"
    base_python.write_bytes(b"base-python")
    base_python.chmod(0o755)
    expected_python.symlink_to(base_python)

    entry_script = root / "kokoro_cli.py"
    foreign = KokoroProviderAdapter(
        Settings(
            root_dir=tmp_path,
            kokoro_base_path="kokoro",
            kokoro_command=f'"{base_python}" "{entry_script}"',
        )
    )
    monkeypatch.setattr(
        foreign,
        "list_voices",
        lambda profile=None: (VoiceProviderVoice(voice_id="af_heart", display_name="AF Heart", language="en-us"),),
    )

    result = foreign.readiness(profile)

    assert result.ready is False
    assert result.issue_code == "TTS_KOKORO_SETUP_INCOMPLETE"


def test_kokoro_execution_identity_rejects_foreign_configured_runtime(tmp_path: Path, monkeypatch) -> None:
    adapter, profile, _root, _expected_python = _prepare_runtime(
        tmp_path,
        monkeypatch,
        marker={"runtime_response_calibration": runtime_response_measurement(0.72)},
        command_variant="foreign_executable",
    )

    with pytest.raises(AppError) as exc_info:
        adapter.execution_identity(profile)

    assert exc_info.value.error_code == "TTS_KOKORO_SETUP_INCOMPLETE"


def test_kokoro_execution_identity_binds_frozen_runtime_calibration(tmp_path: Path, monkeypatch) -> None:
    calibration_a = runtime_response_measurement(0.72)
    adapter, profile, root, _expected_python = _prepare_runtime(
        tmp_path,
        monkeypatch,
        marker={"runtime_response_calibration": calibration_a},
    )

    identity_a = adapter.execution_identity(profile)
    identity_a_repeat = adapter.execution_identity(profile)
    calibration_b = runtime_response_measurement(0.84)
    marker = json.loads((root / ".yas3_kokoro_ready.json").read_text(encoding="utf-8"))
    marker["runtime_response_calibration"] = calibration_b
    (root / ".yas3_kokoro_ready.json").write_text(json.dumps(marker), encoding="utf-8")
    identity_b = adapter.execution_identity(profile)

    assert identity_a["runtime_response_calibration_sha256"] == identity_a_repeat["runtime_response_calibration_sha256"]
    assert identity_a == identity_a_repeat
    assert identity_a["runtime_response_calibration_sha256"] != identity_b["runtime_response_calibration_sha256"]
    assert identity_a != identity_b

