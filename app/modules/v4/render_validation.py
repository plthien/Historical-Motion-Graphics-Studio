from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.modules.v4.render_ffmpeg import run_owned_process


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_ffprobe_json(
    path: Path,
    *,
    settings: Settings | None = None,
    count_frames: bool = False,
    timeout: int = 30,
    operation_deadline_monotonic: float | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
    started_callback: Callable[[int], None] | None = None,
    finished_callback: Callable[[], None] | None = None,
    cancel_grace_seconds: int = 5,
    process_kind: str = "ffprobe",
) -> dict[str, Any]:
    runtime = settings or get_settings()
    args = [runtime.ffprobe_path, "-v", "error", "-show_streams", "-show_format"]
    if count_frames:
        args.append("-count_frames")
    args += ["-of", "json", str(path)]
    deadline = operation_deadline_monotonic or (time.monotonic() + timeout)
    try:
        completed = run_owned_process(
            args,
            operation_deadline_monotonic=deadline,
            cancellation_requested=cancellation_requested or (lambda: False),
            process_kind=process_kind,
            cancel_grace_seconds=cancel_grace_seconds,
            started_callback=started_callback,
            finished_callback=finished_callback,
        )
        if completed["return_code"] != 0:
            raise ValueError("ffprobe returned non-zero")
        payload = json.loads(completed["stdout"])
    except AppError as exc:
        if exc.error_code in {"RENDER_CANCELLED", "RENDER_TIMEOUT"}:
            raise
        raise AppError("RENDER_PROBE_FAILED", "Media probing failed.", 422, retryable=False) from exc
    except Exception as exc:
        raise AppError("RENDER_PROBE_FAILED", "Media probing failed.", 422, retryable=False) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise AppError("RENDER_PROBE_FAILED", "Media probing returned an invalid response.", 422, retryable=False)
    return payload


def probe_visual_input(
    path: Path,
    *,
    declared_mime_type: str | None,
    expected_width: int | None,
    expected_height: int | None,
    settings: Settings | None = None,
    operation_deadline_monotonic: float | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
    started_callback: Callable[[int], None] | None = None,
    finished_callback: Callable[[], None] | None = None,
    cancel_grace_seconds: int = 5,
) -> dict[str, Any]:
    payload = run_ffprobe_json(
        path,
        settings=settings,
        operation_deadline_monotonic=operation_deadline_monotonic,
        cancellation_requested=cancellation_requested,
        started_callback=started_callback,
        finished_callback=finished_callback,
        cancel_grace_seconds=cancel_grace_seconds,
        process_kind="input_probe",
    )
    streams = [item for item in payload["streams"] if item.get("codec_type") == "video"]
    if len(streams) != 1:
        raise AppError("RENDER_INPUT_DECODER_UNAVAILABLE", "Visual input cannot be decoded as exactly one image stream.", 422, retryable=False)
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise AppError("RENDER_VISUAL_DIMENSIONS_INVALID", "Visual input dimensions are invalid.", 422, retryable=False)
    if expected_width is not None and width != expected_width:
        raise AppError("RENDER_VISUAL_DIMENSIONS_INVALID", "Visual input width does not match persisted metadata.", 422, retryable=False)
    if expected_height is not None and height != expected_height:
        raise AppError("RENDER_VISUAL_DIMENSIONS_INVALID", "Visual input height does not match persisted metadata.", 422, retryable=False)
    codec = str(stream.get("codec_name") or "")
    mime_codec = {"image/png": "png", "image/jpeg": "mjpeg", "image/svg+xml": "svg"}.get(declared_mime_type or "")
    if mime_codec and codec != mime_codec:
        raise AppError("RENDER_VISUAL_MIME_MISMATCH", "Visual bytes do not match the declared MIME type.", 422, retryable=False)
    return {"codec": codec, "width": width, "height": height, "pixel_format": stream.get("pix_fmt")}


def parse_mp4_top_level_boxes(path: Path) -> list[dict[str, Any]]:
    size = path.stat().st_size
    boxes: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        offset = 0
        while offset + 8 <= size:
            handle.seek(offset)
            header = handle.read(8)
            box_size = int.from_bytes(header[:4], "big")
            box_type = header[4:8].decode("latin-1")
            header_size = 8
            if box_size == 1:
                extended = handle.read(8)
                if len(extended) != 8:
                    raise AppError("RENDER_MP4_BOX_INVALID", "MP4 extended box header is truncated.", 422, retryable=False)
                box_size = int.from_bytes(extended, "big")
                header_size = 16
            elif box_size == 0:
                box_size = size - offset
            if box_size < header_size or offset + box_size > size:
                raise AppError("RENDER_MP4_BOX_INVALID", "MP4 top-level box structure is invalid.", 422, retryable=False)
            boxes.append({"type": box_type, "offset": offset, "size": box_size})
            offset += box_size
        if offset != size:
            raise AppError("RENDER_MP4_BOX_INVALID", "MP4 file has trailing or truncated box bytes.", 422, retryable=False)
    if not boxes:
        raise AppError("RENDER_MP4_BOX_INVALID", "MP4 file contains no readable boxes.", 422, retryable=False)
    return boxes


def validate_faststart(path: Path) -> bool:
    boxes = parse_mp4_top_level_boxes(path)
    moov = next((item["offset"] for item in boxes if item["type"] == "moov"), None)
    mdat = next((item["offset"] for item in boxes if item["type"] == "mdat"), None)
    if moov is None or mdat is None or moov >= mdat:
        raise AppError("RENDER_FASTSTART_INVALID", "MP4 faststart validation failed.", 422, retryable=False)
    return True


def validate_decode(
    path: Path,
    *,
    subtitle_expected: bool = False,
    settings: Settings | None = None,
    timeout: int = 120,
    operation_deadline_monotonic: float | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
    started_callback: Callable[[int], None] | None = None,
    finished_callback: Callable[[], None] | None = None,
    cancel_grace_seconds: int = 5,
) -> bool:
    runtime = settings or get_settings()
    args = [runtime.ffmpeg_path, "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"]
    deadline = operation_deadline_monotonic or (time.monotonic() + timeout)
    try:
        decoded = run_owned_process(
            args,
            operation_deadline_monotonic=deadline,
            cancellation_requested=cancellation_requested or (lambda: False),
            process_kind="decode_validation",
            cancel_grace_seconds=cancel_grace_seconds,
            started_callback=started_callback,
            finished_callback=finished_callback,
        )
        if decoded["return_code"] != 0:
            raise ValueError("video/audio decode returned non-zero")
        if subtitle_expected:
            subtitle = run_owned_process(
                [runtime.ffmpeg_path, "-v", "error", "-xerror", "-i", str(path), "-map", "0:s:0", "-c:s", "copy", "-f", "null", "-"],
                operation_deadline_monotonic=deadline,
                cancellation_requested=cancellation_requested or (lambda: False),
                process_kind="subtitle_decode_validation",
                cancel_grace_seconds=cancel_grace_seconds,
                started_callback=started_callback,
                finished_callback=finished_callback,
            )
            if subtitle["return_code"] != 0:
                raise ValueError("subtitle decode returned non-zero")
    except AppError as exc:
        if exc.error_code in {"RENDER_CANCELLED", "RENDER_TIMEOUT"}:
            raise
        raise AppError("RENDER_DECODE_VALIDATION_FAILED", "Final video failed strict decode validation.", 422, retryable=False) from exc
    except Exception as exc:
        raise AppError("RENDER_DECODE_VALIDATION_FAILED", "Final video failed strict decode validation.", 422, retryable=False) from exc
    return True


def _parse_ratio(value: str | None) -> tuple[int, int]:
    if not value or "/" not in value:
        return (0, 1)
    left, right = value.split("/", 1)
    return int(left), int(right or 1)


def _canonical_pixel_format(value: Any) -> str:
    """Canonicalize FFmpeg's deprecated YUVJ alias for layout comparison.

    FFmpeg defines YUVJ420P as the same planar 4:2:0 storage layout as
    YUV420P with full/JPEG color range carried separately. Render profiles
    currently contract pixel layout but do not contract color range, so
    validation must not reject a full-range stream solely because ffprobe
    reports the deprecated alias.
    """

    raw = str(value or "").strip().lower()
    if raw == "yuvj420p":
        return "yuv420p"
    return raw


def validate_final_video(
    path: Path,
    *,
    profile: dict[str, Any],
    expected_total_frames: int,
    assembly_duration_time_units: int,
    maximum_bytes: int,
    settings: Settings | None = None,
    operation_deadline_monotonic: float | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
    started_callback: Callable[[int], None] | None = None,
    finished_callback: Callable[[], None] | None = None,
    cancel_grace_seconds: int = 5,
) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise AppError("RENDER_OUTPUT_MISSING", "Rendered output file is missing.", 422, retryable=True)
    size = path.stat().st_size
    if size <= 0:
        raise AppError("RENDER_OUTPUT_EMPTY", "Rendered output file is empty.", 422, retryable=True)
    if size > maximum_bytes:
        raise AppError("RENDER_OUTPUT_TOO_LARGE", "Rendered output exceeds the configured byte limit.", 413, retryable=False)
    boxes = parse_mp4_top_level_boxes(path)
    if boxes[0]["type"] not in {"ftyp", "moov"}:
        raise AppError("RENDER_CONTAINER_INVALID", "Rendered output is not a compatible MP4 container.", 422, retryable=False)
    payload = run_ffprobe_json(
        path,
        settings=settings,
        count_frames=True,
        timeout=60,
        operation_deadline_monotonic=operation_deadline_monotonic,
        cancellation_requested=cancellation_requested,
        started_callback=started_callback,
        finished_callback=finished_callback,
        cancel_grace_seconds=cancel_grace_seconds,
        process_kind="final_probe",
    )
    streams = payload["streams"]
    video = [item for item in streams if item.get("codec_type") == "video"]
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    subtitles = [item for item in streams if item.get("codec_type") == "subtitle"]
    unexpected = [item for item in streams if item.get("codec_type") not in {"video", "audio", "subtitle"}]
    if len(video) != 1 or len(audio) != 1 or unexpected:
        raise AppError("RENDER_STREAM_CONTRACT_INVALID", "Final video stream ownership is invalid.", 422, retryable=False)
    expected_subtitle_count = 1 if profile["subtitle_mode"] == "embedded_mov_text" else 0
    if len(subtitles) != expected_subtitle_count:
        raise AppError("RENDER_SUBTITLE_STREAM_INVALID", "Final video subtitle stream does not match the render profile.", 422, retryable=False)
    v, a = video[0], audio[0]
    fps = _parse_ratio(str(v.get("avg_frame_rate") or v.get("r_frame_rate") or ""))
    expected_fps = (int(profile["frame_rate"]["numerator"]), int(profile["frame_rate"]["denominator"]))
    frame_count = int(v.get("nb_read_frames") or v.get("nb_frames") or 0)
    expected_pixel_format = str(profile["pixel_format"])
    actual_pixel_format = str(v.get("pix_fmt") or "")
    canonical_expected_pixel_format = _canonical_pixel_format(expected_pixel_format)
    canonical_actual_pixel_format = _canonical_pixel_format(actual_pixel_format)
    actual_color_range = v.get("color_range")
    checks = {
        "video_codec": v.get("codec_name") == "h264",
        "width": int(v.get("width") or 0) == int(profile["width"]),
        "height": int(v.get("height") or 0) == int(profile["height"]),
        "pixel_format": canonical_actual_pixel_format == canonical_expected_pixel_format,
        "fps": fps[0] * expected_fps[1] == expected_fps[0] * fps[1],
        "frame_count": frame_count == expected_total_frames,
        "audio_codec": a.get("codec_name") == "aac",
        "audio_sample_rate": int(a.get("sample_rate") or 0) == 48000,
        "audio_channels": int(a.get("channels") or 0) == 2,
        "subtitle_codec": not subtitles or subtitles[0].get("codec_name") == "mov_text",
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise AppError(
            "RENDER_STREAM_CONTRACT_INVALID",
            "Final video technical stream contract is invalid.",
            422,
            retryable=False,
            details={
                "failed_checks": failed,
                "expected_total_frames": expected_total_frames,
                "actual_frame_count": frame_count,
                "expected_frame_rate": {"numerator": expected_fps[0], "denominator": expected_fps[1]},
                "actual_frame_rate": {"numerator": fps[0], "denominator": fps[1]},
                "expected_pixel_format": expected_pixel_format,
                "actual_pixel_format": actual_pixel_format,
                "canonical_actual_pixel_format": canonical_actual_pixel_format,
                "actual_color_range": actual_color_range,
            },
        )
    frame_start_tolerance = int(profile["frame_rate"]["denominator"]) / int(profile["frame_rate"]["numerator"])
    start_times = [float(v.get("start_time") or 0), float(a.get("start_time") or 0)]
    start_times.extend(float(item.get("start_time") or 0) for item in subtitles)
    if any(value < -1e-6 or value > frame_start_tolerance + 1e-6 for value in start_times):
        raise AppError("RENDER_STREAM_START_TIME_INVALID", "Final video streams do not begin within the one-frame technical tolerance.", 422, retryable=False)
    video_duration = float(v.get("duration") or payload.get("format", {}).get("duration") or 0)
    audio_duration = float(a.get("duration") or payload.get("format", {}).get("duration") or 0)
    container_duration = float(payload.get("format", {}).get("duration") or max(video_duration, audio_duration))
    fps_seconds = expected_fps[1] / expected_fps[0]
    aac_tolerance = 1024 / 48000
    expected_audio = assembly_duration_time_units / 1_000_000
    quantized_video = expected_total_frames * fps_seconds
    if abs(video_duration - quantized_video) > fps_seconds + 1e-6:
        raise AppError("RENDER_DURATION_INVALID", "Video duration exceeds one-frame tolerance.", 422, retryable=False)
    if abs(audio_duration - expected_audio) > aac_tolerance + 1e-3:
        raise AppError("RENDER_DURATION_INVALID", "Audio duration exceeds deterministic AAC-frame tolerance.", 422, retryable=False)
    validate_faststart(path)
    validate_decode(
        path,
        subtitle_expected=bool(subtitles),
        settings=settings,
        operation_deadline_monotonic=operation_deadline_monotonic,
        cancellation_requested=cancellation_requested,
        started_callback=started_callback,
        finished_callback=finished_callback,
        cancel_grace_seconds=cancel_grace_seconds,
    )
    sha = sha256_path(path)
    return {
        "schema_version": "2.0", "valid": True,
        "stream_metadata": {
            "video_codec": "h264", "width": int(v["width"]), "height": int(v["height"]),
            "pixel_format": canonical_actual_pixel_format, "ffprobe_pixel_format": actual_pixel_format,
            "color_range": actual_color_range,
            "frame_rate": {"numerator": expected_fps[0], "denominator": expected_fps[1]},
            "frame_count": frame_count, "audio_codec": "aac", "audio_sample_rate_hz": 48000,
            "audio_channels": 2, "subtitle_stream_count": len(subtitles),
            "subtitle_codec": subtitles[0].get("codec_name") if subtitles else None,
        },
        "durations": {"expected_audio_seconds": expected_audio, "quantized_video_seconds": quantized_video, "actual_video_seconds": video_duration, "actual_audio_seconds": audio_duration, "actual_container_seconds": container_duration, "video_tolerance_seconds": fps_seconds, "audio_tolerance_seconds": aac_tolerance},
        "faststart": True, "decode_valid": True, "sha256": sha, "byte_count": size,
    }
