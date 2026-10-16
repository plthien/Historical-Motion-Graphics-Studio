from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_setup_bat_is_the_one_command_entrypoint() -> None:
    source = (ROOT / "kokoro" / "setup_kokoro.bat").read_text(encoding="utf-8")
    assert "setup_kokoro.ps1" in source
    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass" in source


def test_setup_script_uses_isolated_kokoro_runtime_and_model_files() -> None:
    source = (ROOT / "kokoro" / "setup_kokoro.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $KokoroDir ".venv"' in source
    assert 'Join-Path $KokoroDir "requirements.txt"' in source
    assert "kokoro-v1.0.onnx" in source
    assert "voices-v1.0.bin" in source
    assert "Invoke-WebRequest" in source
    assert "kokoro_cli.py" in source
    assert "--output-format" in source
    assert "wav_pcm_s16le" in source
    assert 'Join-Path $RepoRoot ".venv"' not in source
    assert 'Set-Content -LiteralPath $RepoRoot' not in source
    assert "Out-File" not in source


def test_setup_marker_is_invalidated_before_mutable_work_and_written_after_smoke() -> None:
    source = (ROOT / "kokoro" / "setup_kokoro.ps1").read_text(encoding="utf-8")
    main = source[source.index("try {") :]

    assert '.yas3_kokoro_ready.json' in source
    assert "Remove-ReadyMarker" in source
    assert "Write-ReadyMarker" in source
    assert main.index("Remove-ReadyMarker") < main.index("Ensure-Venv")
    assert main.index("Invoke-SmokeTest") < main.index("Invoke-RuntimeResponseCalibration") < main.index("Write-ReadyMarker")
    assert '$CliPath "--measure-runtime-response" "--format" "json"' in source
    assert "flow_version = $FlowVersion" in source
    assert "schema_version" not in source
    assert "requirements_sha256" in source
    assert "wrapper_source_sha256" in source
    assert "runtime_response_calibration_source_sha256" in source
    assert "python_executable_sha256" in source
    assert "runtime_packages" in source
    assert "model_sha256" in source
    assert "voices_sha256" in source
    assert "runtime_response_calibration = $RuntimeResponseCalibration" in source
    assert "wrapper_contract = \"kokoro_command_v3\"" in source
    assert '$env:PYTHONNOUSERSITE = "1"' in source
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        assert key in source


def test_kokoro_dependencies_are_not_added_to_root_requirements() -> None:
    root_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    kokoro_requirements = (ROOT / "kokoro" / "requirements.txt").read_text(encoding="utf-8")

    assert "kokoro-onnx" not in root_requirements
    assert "onnxruntime" not in root_requirements
    assert "kokoro-onnx==0.6.1" in kokoro_requirements
    assert "onnxruntime==1.29.0" in kokoro_requirements
    assert "numpy>=2.0.2,<3" in kokoro_requirements


def test_candidate_audit_validates_isolated_kokoro_dependencies() -> None:
    source = (ROOT / ".github" / "workflows" / "scene-assets-v2-candidate-audit.yml").read_text(encoding="utf-8")

    assert "Validate isolated Kokoro Python dependencies" in source
    assert "python -m venv" in source
    assert "kokoro/requirements.txt" in source
    assert "import kokoro_onnx, onnxruntime" in source
    assert "kokoro-v1.0.onnx" not in source
    assert "voices-v1.0.bin" not in source


def test_candidate_audit_triggers_main_pull_requests() -> None:
    source = (ROOT / ".github" / "workflows" / "scene-assets-v2-candidate-audit.yml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(source)
    events = parsed.get("on") or parsed.get(True)

    assert parsed["name"] == "Main Candidate Audit"
    assert events["pull_request"]["branches"] == ["main"]
    assert events["push"]["branches"] == ["main"]
    assert "fix/voice-runtime-catalog-discovery" not in source
    assert "scene-assets-v2-redesign" not in source
    assert "durable-media-workflow" not in source


def test_gitignore_excludes_runtime_assets() -> None:
    source = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "kokoro/.venv/" in source
    assert "kokoro/*.onnx" in source
    assert "kokoro/voices-*.bin" in source
    assert "kokoro/*.wav" in source
    assert "kokoro/.yas3_kokoro_ready.json" in source
