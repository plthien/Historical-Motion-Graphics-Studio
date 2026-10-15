from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from kokoro.runtime_response_calibration import KOKORO_FLOW_VERSION
from tests.support.kokoro_runtime_response import runtime_response_measurement


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _clear_asset_cache() -> None:
    with KokoroProviderAdapter._asset_fingerprint_lock:
        KokoroProviderAdapter._asset_fingerprint_cache.clear()


def _write_runtime(root: Path, *, model: bytes = b"model-bytes", voices: bytes = b"voices-bytes") -> tuple[Path, Path]:
    python_path = KokoroProviderAdapter._expected_venv_python(root)
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"python")
    requirements = root / KokoroProviderAdapter._REQUIREMENTS_FILENAME
    wrapper = root / "kokoro_cli.py"
    response_source = root / KokoroProviderAdapter._RUNTIME_RESPONSE_SOURCE_FILENAME
    model_path = root / KokoroProviderAdapter._MODEL_FILENAME
    voices_path = root / KokoroProviderAdapter._VOICES_FILENAME
    requirements.write_text("kokoro-onnx==0.6.1\n", encoding="utf-8")
    wrapper.write_text("# test wrapper\n", encoding="utf-8")
    response_source.write_text("# test calibration source\n", encoding="utf-8")
    model_path.write_bytes(model)
    voices_path.write_bytes(voices)

    site_packages = KokoroProviderAdapter._runtime_site_packages(root)
    metadata = site_packages / "kokoro_onnx-0.6.1.dist-info" / "METADATA"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text("Name: kokoro-onnx\nVersion: 0.6.1\n", encoding="utf-8")
    return model_path, voices_path


def _write_marker(root: Path, *, model_hash: str, voices_hash: str) -> None:
    requirements_path = root / KokoroProviderAdapter._REQUIREMENTS_FILENAME
    wrapper_path = root / "kokoro_cli.py"
    response_source_path = root / KokoroProviderAdapter._RUNTIME_RESPONSE_SOURCE_FILENAME
    python_path = KokoroProviderAdapter._expected_venv_python(root)
    marker = {
        "flow_version": KOKORO_FLOW_VERSION,
        "python_version": "3.12.0",
        "python_executable_sha256": _sha256(python_path.read_bytes()),
        "requirements_sha256": _sha256(requirements_path.read_bytes()),
        "wrapper_source_sha256": _sha256(wrapper_path.read_bytes()),
        "runtime_response_calibration_source_sha256": _sha256(response_source_path.read_bytes()),
        "runtime_packages": KokoroProviderAdapter._installed_runtime_packages(root),
        "model_sha256": model_hash,
        "voices_sha256": voices_hash,
        "wrapper_contract": KokoroProviderAdapter._WRAPPER_CONTRACT,
        "canonical_sample_rate_hz": 48_000,
        "output_format": "wav_pcm_s16le",
        "runtime_response_calibration": runtime_response_measurement(),
    }
    (root / KokoroProviderAdapter._READY_MARKER_FILENAME).write_text(json.dumps(marker), encoding="utf-8")


def test_kokoro_runtime_readiness_accepts_marker_and_current_asset_bytes(tmp_path: Path, monkeypatch) -> None:
    _clear_asset_cache()
    model_path, voices_path = _write_runtime(tmp_path)
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_MODEL_SHA256", _sha256(model_path.read_bytes()))
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_VOICES_SHA256", _sha256(voices_path.read_bytes()))
    _write_marker(
        tmp_path,
        model_hash=KokoroProviderAdapter._EXPECTED_MODEL_SHA256,
        voices_hash=KokoroProviderAdapter._EXPECTED_VOICES_SHA256,
    )

    assert KokoroProviderAdapter._runtime_setup_readiness(tmp_path) is None


def test_kokoro_runtime_readiness_rejects_noncurrent_flow_marker(tmp_path: Path, monkeypatch) -> None:
    _clear_asset_cache()
    model_path, voices_path = _write_runtime(tmp_path)
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_MODEL_SHA256", _sha256(model_path.read_bytes()))
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_VOICES_SHA256", _sha256(voices_path.read_bytes()))
    _write_marker(
        tmp_path,
        model_hash=KokoroProviderAdapter._EXPECTED_MODEL_SHA256,
        voices_hash=KokoroProviderAdapter._EXPECTED_VOICES_SHA256,
    )
    marker_path = tmp_path / KokoroProviderAdapter._READY_MARKER_FILENAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["flow_version"] = "not-current"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    readiness = KokoroProviderAdapter._runtime_setup_readiness(tmp_path)

    assert readiness is not None
    assert readiness.issue_code == "TTS_KOKORO_SETUP_INCOMPLETE"


def test_kokoro_runtime_readiness_blocks_when_marker_is_valid_but_model_bytes_changed(tmp_path: Path, monkeypatch) -> None:
    _clear_asset_cache()
    model_path, voices_path = _write_runtime(tmp_path)
    expected_model_hash = _sha256(model_path.read_bytes())
    expected_voices_hash = _sha256(voices_path.read_bytes())
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_MODEL_SHA256", expected_model_hash)
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_VOICES_SHA256", expected_voices_hash)
    _write_marker(tmp_path, model_hash=expected_model_hash, voices_hash=expected_voices_hash)
    model_path.write_bytes(b"tampered-model")

    readiness = KokoroProviderAdapter._runtime_setup_readiness(tmp_path)

    assert readiness is not None
    assert readiness.ready is False
    assert readiness.issue_code == KokoroProviderAdapter._INCOMPLETE_SETUP_CODE


def test_kokoro_runtime_readiness_blocks_when_marker_is_valid_but_voices_bytes_changed(tmp_path: Path, monkeypatch) -> None:
    _clear_asset_cache()
    model_path, voices_path = _write_runtime(tmp_path)
    expected_model_hash = _sha256(model_path.read_bytes())
    expected_voices_hash = _sha256(voices_path.read_bytes())
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_MODEL_SHA256", expected_model_hash)
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_VOICES_SHA256", expected_voices_hash)
    _write_marker(tmp_path, model_hash=expected_model_hash, voices_hash=expected_voices_hash)
    voices_path.write_bytes(b"tampered-voices")

    readiness = KokoroProviderAdapter._runtime_setup_readiness(tmp_path)

    assert readiness is not None
    assert readiness.ready is False
    assert readiness.issue_code == KokoroProviderAdapter._INCOMPLETE_SETUP_CODE


def test_kokoro_runtime_readiness_uses_stat_keyed_asset_hash_cache(tmp_path: Path, monkeypatch) -> None:
    _clear_asset_cache()
    model_path, voices_path = _write_runtime(tmp_path)
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_MODEL_SHA256", _sha256(model_path.read_bytes()))
    monkeypatch.setattr(KokoroProviderAdapter, "_EXPECTED_VOICES_SHA256", _sha256(voices_path.read_bytes()))
    _write_marker(
        tmp_path,
        model_hash=KokoroProviderAdapter._EXPECTED_MODEL_SHA256,
        voices_hash=KokoroProviderAdapter._EXPECTED_VOICES_SHA256,
    )
    original = KokoroProviderAdapter._stream_sha256.__func__
    calls: list[Path] = []

    def recording_stream_sha256(cls, path: Path, *, chunk_size: int = KokoroProviderAdapter._ASSET_HASH_CHUNK_BYTES) -> str:
        calls.append(path)
        return original(cls, path, chunk_size=chunk_size)

    monkeypatch.setattr(KokoroProviderAdapter, "_stream_sha256", classmethod(recording_stream_sha256))

    assert KokoroProviderAdapter._runtime_setup_readiness(tmp_path) is None
    assert KokoroProviderAdapter._runtime_setup_readiness(tmp_path) is None

    assert calls.count(model_path) == 1
    assert calls.count(voices_path) == 1
