from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.api.errors import AppError
from app.modules.v4 import render_validation
from app.services.render_settings import default_render_settings


def _profile() -> dict:
    profile = deepcopy(default_render_settings()["profiles"][0])
    profile["subtitle_mode"] = "embedded_mov_text"
    return profile


def _probe_payload(*, pixel_format: str, color_range: str | None) -> dict:
    video = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": pixel_format,
        "avg_frame_rate": "30/1",
        "nb_read_frames": "30",
        "start_time": "0.000000",
        "duration": "1.000000",
    }
    if color_range is not None:
        video["color_range"] = color_range
    return {
        "streams": [
            video,
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "start_time": "0.000000",
                "duration": "1.000000",
            },
            {
                "codec_type": "subtitle",
                "codec_name": "mov_text",
                "start_time": "0.000000",
            },
        ],
        "format": {"duration": "1.000000"},
    }


def _patch_successful_container_validation(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
) -> None:
    monkeypatch.setattr(render_validation, "parse_mp4_top_level_boxes", lambda _path: [{"type": "ftyp", "offset": 0, "size": 8}])
    monkeypatch.setattr(render_validation, "run_ffprobe_json", lambda *_args, **_kwargs: payload)
    monkeypatch.setattr(render_validation, "validate_faststart", lambda _path: True)
    monkeypatch.setattr(render_validation, "validate_decode", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(render_validation, "sha256_path", lambda _path: "a" * 64)


def test_yuvj420p_full_range_satisfies_yuv420p_layout_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "final_video.mp4.part"
    output.write_bytes(b"mock-mp4")
    _patch_successful_container_validation(
        monkeypatch,
        _probe_payload(pixel_format="yuvj420p", color_range="pc"),
    )

    result = render_validation.validate_final_video(
        output,
        profile=_profile(),
        expected_total_frames=30,
        assembly_duration_time_units=1_000_000,
        maximum_bytes=1024,
    )

    stream = result["stream_metadata"]
    assert stream["pixel_format"] == "yuv420p"
    assert stream["ffprobe_pixel_format"] == "yuvj420p"
    assert stream["color_range"] == "pc"


def test_distinct_yuv422p_layout_still_fails_pixel_format_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "final_video.mp4.part"
    output.write_bytes(b"mock-mp4")
    _patch_successful_container_validation(
        monkeypatch,
        _probe_payload(pixel_format="yuv422p", color_range="tv"),
    )

    with pytest.raises(AppError) as exc_info:
        render_validation.validate_final_video(
            output,
            profile=_profile(),
            expected_total_frames=30,
            assembly_duration_time_units=1_000_000,
            maximum_bytes=1024,
        )

    error = exc_info.value
    assert error.error_code == "RENDER_STREAM_CONTRACT_INVALID"
    assert error.details["failed_checks"] == ["pixel_format"]
    assert error.details["expected_pixel_format"] == "yuv420p"
    assert error.details["actual_pixel_format"] == "yuv422p"
    assert error.details["canonical_actual_pixel_format"] == "yuv422p"
    assert error.details["actual_color_range"] == "tv"
