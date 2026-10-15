from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any

from app.api.errors import AppError

COMMAND_GRAPH_VERSION = "ffmpeg_render_graph_v2"
FILTER_SCRIPT_VERSION = "ffmpeg_filter_script_v1"
MOTION_FILTER_VERSION = "ffmpeg_motion_filter_v2"
NORMALIZED_CROP_VERSION = "ffmpeg_normalized_crop_v2"
_RECTANGLE_EPSILON = 1e-9


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_json_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _normalized_anchor(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError("RENDER_MOTION_INVALID", f"{field} must be numeric.", 409, retryable=False) from exc
    if not math.isfinite(number) or not (0.0 <= number <= 1.0):
        raise AppError("RENDER_MOTION_INVALID", f"{field} is outside normalized bounds.", 409, retryable=False)
    return number


def _normalized_rectangle(value: Any, *, field: str) -> dict[str, float]:
    if isinstance(value, dict) and isinstance(value.get("crop"), dict):
        value = value["crop"]
    if not isinstance(value, dict):
        raise AppError("RENDER_MOTION_INVALID", f"{field} must contain a normalized crop rectangle.", 409, retryable=False)
    result: dict[str, float] = {}
    for key, default in (("x", 0.0), ("y", 0.0), ("width", 1.0), ("height", 1.0)):
        try:
            number = float(value.get(key, default))
        except (TypeError, ValueError) as exc:
            raise AppError("RENDER_MOTION_INVALID", f"{field}.{key} must be numeric.", 409, retryable=False) from exc
        if not math.isfinite(number):
            raise AppError("RENDER_MOTION_INVALID", f"{field}.{key} must be finite.", 409, retryable=False)
        if key in {"width", "height"}:
            if not (0.0 < number <= 1.0):
                raise AppError("RENDER_MOTION_INVALID", f"{field}.{key} is outside normalized bounds.", 409, retryable=False)
        elif not (0.0 <= number <= 1.0):
            raise AppError("RENDER_MOTION_INVALID", f"{field}.{key} is outside normalized bounds.", 409, retryable=False)
        result[key] = number
    if result["x"] + result["width"] > 1.0 + _RECTANGLE_EPSILON:
        raise AppError("RENDER_MOTION_INVALID", f"{field} exceeds the normalized source width.", 409, retryable=False)
    if result["y"] + result["height"] > 1.0 + _RECTANGLE_EPSILON:
        raise AppError("RENDER_MOTION_INVALID", f"{field} exceeds the normalized source height.", 409, retryable=False)
    return result


def translate_motion_keyframes(visual_instruction: dict[str, Any], *, frame_count: int, output_width: int, output_height: int) -> dict[str, Any]:
    if frame_count < 1:
        raise AppError("RENDER_MOTION_INVALID", "Motion frame count must be positive.", 409, retryable=False)
    keyframes = deepcopy(visual_instruction.get("motion_keyframes") or [])
    if not keyframes:
        keyframes = [{"time_fraction": 0.0, "crop": visual_instruction.get("start_crop")}, {"time_fraction": 1.0, "crop": visual_instruction.get("end_crop")}]
    if len(keyframes) > 256:
        raise AppError("RENDER_MOTION_KEYFRAME_LIMIT", "Motion keyframe count exceeds the compiler bound.", 413, retryable=False)
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(keyframes):
        try:
            fraction = float(item.get("offset", item.get("time_fraction", item.get("progress", 0.0 if index == 0 else 1.0))))
        except (TypeError, ValueError) as exc:
            raise AppError("RENDER_MOTION_INVALID", "Motion keyframe offset must be numeric.", 409, retryable=False) from exc
        if not math.isfinite(fraction) or not (0.0 <= fraction <= 1.0):
            raise AppError("RENDER_MOTION_INVALID", "Motion keyframe offset is outside normalized bounds.", 409, retryable=False)
        crop = item.get("crop") or item.get("normalized_crop") or visual_instruction.get("start_crop") or {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        values = _normalized_rectangle(crop, field=f"motion_keyframes[{index}].crop")
        normalized.append({"frame": round(fraction * max(frame_count - 1, 0)), **values})
    normalized.sort(key=lambda item: item["frame"])
    # A one-frame clip cannot express motion interpolation. Preserve the
    # authoritative initial crop deterministically instead of rejecting the
    # valid interval because all normalized keyframes quantize to frame zero.
    if frame_count == 1:
        normalized = [normalized[0]]
    coalesced: list[dict[str, Any]] = []
    for item in normalized:
        if coalesced and item["frame"] == coalesced[-1]["frame"]:
            if all(math.isclose(float(item[key]), float(coalesced[-1][key]), abs_tol=1e-12) for key in ("x", "y", "width", "height")):
                continue
            raise AppError("RENDER_MOTION_DUPLICATE_FRAME", "Motion keyframes contain an ambiguous duplicate frame.", 409, retryable=False)
        coalesced.append(item)
    if coalesced[0]["frame"] != 0 or (frame_count > 1 and coalesced[-1]["frame"] != frame_count - 1):
        raise AppError("RENDER_MOTION_INVALID", "Motion keyframes must own the first and final clip frames.", 409, retryable=False)
    return {
        "motion_type": str(visual_instruction.get("motion_type", "static")),
        "fit_mode": str(visual_instruction.get("fit_mode", "cover")),
        "anchor_x": _normalized_anchor(visual_instruction.get("anchor_x", 0.5), field="visual_instruction.anchor_x"),
        "anchor_y": _normalized_anchor(visual_instruction.get("anchor_y", 0.5), field="visual_instruction.anchor_y"),
        "background_fill": str(visual_instruction.get("background_fill", "technical_black")),
        "output_width": output_width,
        "output_height": output_height,
        "keyframes": coalesced,
        "interpolation": "piecewise_linear_by_frame_v1",
        "motion_filter_version": MOTION_FILTER_VERSION,
        "normalized_crop_version": NORMALIZED_CROP_VERSION,
    }


def compile_ffmpeg_render_graph(
    *,
    assembly_plan: dict[str, Any],
    render_profile: dict[str, Any],
    frame_quantization: dict[str, Any],
    input_descriptors: list[dict[str, Any]],
) -> dict[str, Any]:
    descriptor_map = {str(item["artifact_id"]): deepcopy(item) for item in input_descriptors}
    if len(descriptor_map) != len(input_descriptors):
        raise AppError("RENDER_COMMAND_GRAPH_INVALID", "Render input Artifact descriptors must be unique.", 409, retryable=False)
    intervals_by_beat = {item["beat_id"]: item for item in frame_quantization["intervals"]}
    visual_nodes: list[dict[str, Any]] = []
    for beat in assembly_plan["beats"]:
        quantized = intervals_by_beat.get(beat["beat_id"])
        if quantized is None:
            raise AppError("RENDER_COMMAND_GRAPH_INVALID", "Frame quantization does not cover every Assembly beat.", 409, retryable=False)
        artifact_id = beat.get("selected_visual_artifact_id")
        if artifact_id not in descriptor_map:
            raise AppError("RENDER_VISUAL_INPUT_INVALID", "Assembly visual Artifact is absent from validated input descriptors.", 409, retryable=False, details={"artifact_id": artifact_id})
        visual_nodes.append({
            "node_type": "visual_clip", "scene_id": beat["scene_id"], "beat_id": beat["beat_id"],
            "beat_order": beat["beat_order"],
            "artifact_id": artifact_id, "frame_count": quantized["frame_count"],
            "motion": translate_motion_keyframes(beat["visual_instruction"], frame_count=quantized["frame_count"], output_width=render_profile["width"], output_height=render_profile["height"]),
        })
    graph = {
        "schema_version": "3.0", "compiler_version": COMMAND_GRAPH_VERSION,
        "profile": deepcopy(render_profile),
        "ordered_input_descriptors": sorted(input_descriptors, key=lambda item: (int(item["input_index"]), str(item["artifact_id"]))),
        "visual_nodes": visual_nodes,
        "audio_nodes": {
            "narration": deepcopy(assembly_plan["narration_track"]),
            "background_music": deepcopy(assembly_plan.get("background_audio_track")),
            "sound_effects": deepcopy(assembly_plan.get("sfx_track", [])),
            "mix_policy": {"normalization": False, "output_sample_rate_hz": 48000, "output_channels": 2},
        },
        "subtitle_mapping": {
            "mode": render_profile["subtitle_mode"],
            "srt_artifact_id": assembly_plan["source_subtitle_srt_artifact_id"],
            "vtt_artifact_id": assembly_plan["source_subtitle_vtt_artifact_id"],
        },
        "concat": {"node_type": "concat", "ordered_beat_ids": [beat["beat_id"] for beat in assembly_plan["beats"]], "expected_total_frames": frame_quantization["total_frame_count"]},
        "output": {
            "container": "mp4", "video_codec": "libx264", "pixel_format": "yuv420p",
            "audio_codec": "aac", "audio_sample_rate_hz": 48000, "audio_channels": 2,
            "faststart": True, "constant_frame_rate": True,
        },
    }
    # Public/persisted graph must not contain path authority or command fragments.
    raw = canonical_json_bytes(graph).decode("utf-8")
    forbidden = ["absolute_path", "shell_command", "filter_complex_script_path", "output_path"]
    if any(token in raw for token in forbidden):
        raise AppError("RENDER_COMMAND_GRAPH_PATH_LEAK", "The normalized Render graph contains forbidden path authority.", 500, retryable=False)
    graph["command_graph_sha256"] = canonical_json_sha256(graph)
    return graph
