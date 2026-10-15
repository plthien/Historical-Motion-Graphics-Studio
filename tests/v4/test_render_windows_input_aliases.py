from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.errors import AppError
from app.modules.v4 import render_ffmpeg
from app.modules.v4.render_service import RenderService
from app.modules.v4.windows_subprocess_guard import (
    WINDOWS_CREATE_PROCESS_COMMAND_LIMIT,
    _compact_windows_media_command,
    _windows_command_line_chars,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows command compaction requires a Windows host")
def test_attempt_owned_input_aliases_bound_a_274_input_windows_command(tmp_path: Path) -> None:
    # Keep the 274-input command over the Windows limit even when CI uses a
    # short Linux temp root; its alias paths still represent a Windows launch.
    attempt = tmp_path / "windows-length-calibration" / "attempt"
    source_root = tmp_path / ("long-artifact-segment-" * 4)
    attempt.mkdir(parents=True)
    source_root.mkdir()
    descriptors: list[dict[str, object]] = []
    artifact_paths: dict[str, Path] = {}
    for index in range(274):
        artifact_id = f"artifact_{index:04d}"
        source = source_root / f"immutable-production-visual-{index:04d}.jpg"
        source.write_bytes(f"visual-{index}".encode())
        descriptors.append({"artifact_id": artifact_id, "input_index": index, "kind": "visual"})
        artifact_paths[artifact_id] = source

    aliases = render_ffmpeg._materialize_windows_input_aliases(
        descriptors=descriptors,
        artifact_paths=artifact_paths,
        alias_directory=attempt / render_ffmpeg.WINDOWS_INPUT_ALIAS_DIRECTORY,
    )

    command = [r"C:\ffmpeg\bin\ffmpeg.exe", "-hide_banner", "-nostdin", "-y"]
    for descriptor in descriptors:
        command += ["-loop", "1", "-i", str(aliases[str(descriptor["artifact_id"])])]
    filter_path = attempt / "filter_complex.txt"
    output_path = attempt / "final_video.mp4.part"
    filter_path.write_text("null", encoding="utf-8")
    command += ["-/filter_complex", str(filter_path), "-f", "mp4", str(output_path)]

    assert len(aliases) == 274
    assert all(os.path.samefile(aliases[artifact_id], source) for artifact_id, source in artifact_paths.items())
    assert _windows_command_line_chars(command) >= WINDOWS_CREATE_PROCESS_COMMAND_LIMIT

    compacted, process_cwd = _compact_windows_media_command(command)

    assert process_cwd == str(attempt)
    assert _windows_command_line_chars(compacted) < WINDOWS_CREATE_PROCESS_COMMAND_LIMIT
    assert all(str(source) not in compacted for source in artifact_paths.values())

    RenderService._cleanup_attempt(attempt)
    assert not attempt.exists()
    assert all(source.is_file() for source in artifact_paths.values())


def test_build_ffmpeg_args_switches_to_short_aliases_before_windows_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "immutable.jpg"
    source.write_bytes(b"image")
    filter_path = tmp_path / "attempt" / "filter_complex.txt"
    filter_path.parent.mkdir()
    graph = {
        "profile": {
            "frame_rate": {"numerator": 30, "denominator": 1},
            "video_preset": "medium",
            "video_crf": 18,
            "pixel_format": "yuv420p",
            "audio_bitrate_kbps": 192,
            "encoder_threads": 1,
        },
        "ordered_input_descriptors": [
            {"artifact_id": "artifact_image", "input_index": 0, "kind": "visual"},
        ],
        "subtitle_mapping": {"mode": "none"},
    }
    monkeypatch.setattr(render_ffmpeg, "build_filter_script", lambda *_args, **_kwargs: "null;\n")
    monkeypatch.setattr(render_ffmpeg, "_windows_command_line_chars", lambda _args: render_ffmpeg.WINDOWS_RENDER_COMMAND_SAFETY_LIMIT)
    monkeypatch.setattr(render_ffmpeg.os, "name", "nt")

    args, index_map = render_ffmpeg.build_ffmpeg_args(
        graph=graph,
        artifact_paths={"artifact_image": source},
        filter_script_path=filter_path,
        output_path=filter_path.parent / "final_video.mp4.part",
        settings=SimpleNamespace(ffmpeg_path="ffmpeg"),
    )

    alias = filter_path.parent / render_ffmpeg.WINDOWS_INPUT_ALIAS_DIRECTORY / "i0000.jpg"
    assert index_map == {"artifact_image": 0}
    assert str(alias) in args
    assert str(source) not in args
    assert os.path.samefile(alias, source)


def test_ffmpeg_start_failure_preserves_safe_cause_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_to_start(*_args, **_kwargs):
        raise AppError(
            "RENDER_PROCESS_START_FAILED",
            "A local Render process could not be started.",
            422,
            retryable=True,
            details={"process_kind": "final_encode", "os_error_code": 22, "winerror": 206},
        )

    monkeypatch.setattr(render_ffmpeg, "run_owned_process", fail_to_start)

    with pytest.raises(AppError) as exc_info:
        render_ffmpeg.run_ffmpeg_process(
            ["ffmpeg"],
            total_duration_time_units=1,
            total_frames=1,
            timeout_seconds=1,
            cancel_grace_seconds=1,
            progress_callback=lambda _update: None,
            cancellation_requested=lambda: False,
        )

    error = exc_info.value
    assert error.error_code == "RENDER_FFMPEG_FAILED"
    assert error.details == {
        "cause_error_code": "RENDER_PROCESS_START_FAILED",
        "process_kind": "final_encode",
        "os_error_code": 22,
        "winerror": 206,
    }
