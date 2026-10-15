from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.package_manifest import AUTHORITY_COLLECTIONS, build_manifest, collect_package_files

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tools/manifest_authority/external_authoring_baseline.json"
PATCH = ROOT / "tools/manifest_authority/external_authoring_patch.json"


def test_external_authoring_has_one_current_manifest_baseline_and_patch() -> None:
    files = sorted(path.name for path in (ROOT / "tools/manifest_authority").glob("*.json"))
    assert files == ["external_authoring_baseline.json", "external_authoring_patch.json"]
    patch = json.loads(PATCH.read_text(encoding="utf-8"))
    assert patch["metadata"]["migration_head"] == "029_voice_first_elevenlabs"
    note = patch["metadata"]["manifest_note"].lower()
    assert "one external-authoring flow" in note
    assert "no internal llm or web-research calls" in note
    assert "scene breakdown approval atomically publishes approved scene plan, visual plan, hidden prompt bundle, and beattimingmap" in note
    assert "standalone coordinator" in note
    assert "scene assets v2" in note
    assert "visual references v2 is the canonical live visual reference runtime" in note
    assert "visual references is not owned by redis/rq" in note
    assert "voice is owned by the same durable media workflow host" in note
    assert "voice is not owned by redis/rq" in note
    assert "beattimingmap is scene-owned" in note
    assert "rq production ownership is render only" in note
    assert "generic startup recovery skips visual reference v2 transport" in note
    assert "no compatibility reader" in note


def test_external_authoring_authority_contains_only_existing_current_paths() -> None:
    manifest = build_manifest(ROOT, BASELINE, PATCH)
    for collection in AUTHORITY_COLLECTIONS:
        for value in manifest[collection]:
            assert (ROOT / value).is_file(), value
            assert "/v3/" not in value
            assert "tests/v3" not in value


def test_external_authoring_active_authority_excludes_internal_llm_and_research_contracts() -> None:
    manifest = build_manifest(ROOT, BASELINE, PATCH)
    active = "\n".join(manifest["active_contracts"] + manifest["active_docs"])
    for token in (
        "llm_provider_registry",
        "llm_settings",
        "script_research",
        "research_fact_check",
        "web_research_sources",
        "visual_planner",
    ):
        assert token not in active.lower()


def test_package_manifest_text_identity_is_cross_platform_line_ending_stable(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_bytes(b"alpha\r\nbeta\r\n")
    crlf = collect_package_files(tmp_path)

    sample.write_bytes(b"alpha\nbeta\n")
    lf = collect_package_files(tmp_path)

    assert crlf == lf
    assert lf == [
        {
            "path": "sample.txt",
            "size_bytes": len(b"alpha\nbeta\n"),
            "sha256": hashlib.sha256(b"alpha\nbeta\n").hexdigest(),
        }
    ]


def test_package_manifest_binary_identity_remains_byte_exact(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    first = b"\x00alpha\r\nbeta\r\n"
    second = b"\x00alpha\nbeta\n"

    sample.write_bytes(first)
    first_records = collect_package_files(tmp_path)
    sample.write_bytes(second)
    second_records = collect_package_files(tmp_path)

    assert first_records != second_records
    assert first_records[0]["size_bytes"] == len(first)
    assert second_records[0]["size_bytes"] == len(second)


def test_package_manifest_excludes_local_kokoro_runtime_assets(tmp_path: Path) -> None:
    kokoro = tmp_path / "kokoro"
    kokoro.mkdir()
    (kokoro / "kokoro_cli.py").write_text("print('ok')\n", encoding="utf-8")
    (kokoro / "requirements.txt").write_text("kokoro-onnx==0.6.1\nonnxruntime==1.29.0\nnumpy>=2.0.2,<3\n", encoding="utf-8")
    (kokoro / "kokoro-v1.0.onnx").write_bytes(b"model")
    (kokoro / "voices-v1.0.bin").write_bytes(b"voices")
    (kokoro / "preview.wav").write_bytes(b"wav")
    (kokoro / ".yas3_kokoro_ready.json").write_text("{}\n", encoding="utf-8")

    paths = [record["path"] for record in collect_package_files(tmp_path)]

    assert "kokoro/kokoro_cli.py" in paths
    assert "kokoro/requirements.txt" in paths
    assert "kokoro/kokoro-v1.0.onnx" not in paths
    assert "kokoro/voices-v1.0.bin" not in paths
    assert "kokoro/preview.wav" not in paths
    assert "kokoro/.yas3_kokoro_ready.json" not in paths
