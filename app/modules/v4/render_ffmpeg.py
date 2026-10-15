from __future__ import annotations

import math
import os
import queue
import re
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.modules.v4.windows_subprocess_guard import (
    WINDOWS_RENDER_COMMAND_SAFETY_LIMIT,
    _windows_command_line_chars,
)

PROGRESS_KEYS = {"frame", "out_time_us", "speed", "progress"}
PROGRESS_POLL_SECONDS = 0.05
PROGRESS_QUEUE_MAX_RECORDS = 256
MAX_MOTION_KEYFRAMES = 256
MAX_FILTER_SCRIPT_BYTES = 1_000_000
MOTION_FILTER_VERSION = "ffmpeg_motion_filter_v2"
NORMALIZED_CROP_VERSION = "ffmpeg_normalized_crop_v2"
AUDIO_CLIPPING_ANALYZER_VERSION = "ffmpeg_audio_clipping_analysis_v1"
CLIPPING_THRESHOLD_DBFS = 0.0
PROCESS_STDOUT_LIMIT_BYTES = 256 * 1024
PROCESS_STDERR_LIMIT_BYTES = 512 * 1024
WINDOWS_INPUT_ALIAS_DIRECTORY = "ffmpeg_inputs"
_RECTANGLE_EPSILON = 1e-9
_PEAK_RE = re.compile(r"Peak level dB:\s*([^\s]+)", re.IGNORECASE)


def _volume(gain_db: float) -> str:
    return f"volume={gain_db:.6f}dB"


def _format_number(value: float) -> str:
    if not math.isfinite(value):
        raise AppError("RENDER_MOTION_INVALID", "Motion keyframe values must be finite.", 409, retryable=False)
    return f"{value:.9f}".rstrip("0").rstrip(".") or "0"


def _normalized_crop(value: Any, *, field: str) -> dict[str, float]:
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


def _normalized_anchor(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError("RENDER_MOTION_INVALID", f"{field} must be numeric.", 409, retryable=False) from exc
    if not math.isfinite(number) or not (0.0 <= number <= 1.0):
        raise AppError("RENDER_MOTION_INVALID", f"{field} is outside normalized bounds.", 409, retryable=False)
    return number


def _validated_motion_keyframes(motion: dict[str, Any], *, frame_count: int) -> list[dict[str, float | int]]:
    if frame_count < 1:
        raise AppError("RENDER_MOTION_INVALID", "Motion frame count must be positive.", 409, retryable=False)
    raw = motion.get("keyframes")
    if not isinstance(raw, list) or not raw:
        raise AppError("RENDER_MOTION_INVALID", "Motion graph must contain keyframes.", 409, retryable=False)
    if len(raw) > MAX_MOTION_KEYFRAMES:
        raise AppError("RENDER_MOTION_KEYFRAME_LIMIT", "Motion keyframe count exceeds the configured compiler bound.", 413, retryable=False)
    validated: list[dict[str, float | int]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or isinstance(item.get("frame"), bool):
            raise AppError("RENDER_MOTION_INVALID", "Motion keyframe shape is invalid.", 409, retryable=False)
        try:
            frame = int(item["frame"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError("RENDER_MOTION_INVALID", "Motion keyframe frame must be an integer.", 409, retryable=False) from exc
        if frame < 0 or frame >= frame_count:
            raise AppError("RENDER_MOTION_INVALID", "Motion keyframe frame is outside the clip interval.", 409, retryable=False)
        crop = _normalized_crop(item, field=f"keyframes[{index}]")
        candidate: dict[str, float | int] = {"frame": frame, **crop}
        if validated and frame == int(validated[-1]["frame"]):
            if all(math.isclose(float(candidate[key]), float(validated[-1][key]), abs_tol=1e-12) for key in ("x", "y", "width", "height")):
                continue
            raise AppError("RENDER_MOTION_DUPLICATE_FRAME", "Motion keyframes contain an ambiguous duplicate frame.", 409, retryable=False)
        if validated and frame < int(validated[-1]["frame"]):
            raise AppError("RENDER_MOTION_FRAME_ORDER_INVALID", "Motion keyframes must be strictly ordered by frame.", 409, retryable=False)
        validated.append(candidate)
    if int(validated[0]["frame"]) != 0:
        raise AppError("RENDER_MOTION_INVALID", "The first motion keyframe must apply at frame zero.", 409, retryable=False)
    if frame_count > 1 and int(validated[-1]["frame"]) != frame_count - 1:
        raise AppError("RENDER_MOTION_INVALID", "The last motion keyframe must apply at the final clip frame.", 409, retryable=False)
    return validated


def _piecewise_expression(keyframes: list[dict[str, float | int]], field: str, *, variable: str = "n") -> str:
    first_value = float(keyframes[0][field])
    if all(float(item[field]) == first_value for item in keyframes[1:]):
        return _format_number(first_value)
    if len(keyframes) == 1:
        return _format_number(first_value)
    expression = _format_number(float(keyframes[-1][field]))
    for left, right in reversed(list(zip(keyframes, keyframes[1:]))):
        left_frame = int(left["frame"])
        right_frame = int(right["frame"])
        delta = right_frame - left_frame
        left_value = float(left[field])
        right_value = float(right[field])
        slope = right_value - left_value
        interpolation = f"({_format_number(left_value)}+({_format_number(slope)})*(({variable})-{left_frame})/{delta})"
        expression = f"if(lte(({variable}),{right_frame}),{interpolation},{expression})"
    first = keyframes[0]
    return f"if(lte(({variable}),{int(first['frame'])}),{_format_number(float(first[field]))},{expression})"


def compile_motion_filter(motion: dict[str, Any], *, frame_count: int, width: int, height: int) -> str:
    """Compile exact Phase 13 normalized crop rectangles into FFmpeg filters.

    ``x`` and ``y`` are absolute normalized crop origins in the source frame;
    ``width`` and ``height`` are the authoritative normalized crop size.  The
    compiler maps the selected source rectangle through a per-frame perspective
    transform so FFmpeg always receives constant input/output dimensions.  This
    avoids the undefined/stale geometry produced when a dynamic ``scale`` feeds
    a fixed-geometry ``crop`` filter.
    """
    if motion.get("interpolation") != "piecewise_linear_by_frame_v1":
        raise AppError("RENDER_MOTION_INTERPOLATION_UNSUPPORTED", "Motion interpolation authority is unsupported.", 409, retryable=False)
    if motion.get("motion_filter_version") not in {None, MOTION_FILTER_VERSION}:
        raise AppError("RENDER_MOTION_FILTER_VERSION_UNSUPPORTED", "Motion filter authority is unsupported.", 409, retryable=False)
    if motion.get("normalized_crop_version") not in {None, NORMALIZED_CROP_VERSION}:
        raise AppError("RENDER_NORMALIZED_CROP_VERSION_UNSUPPORTED", "Normalized crop authority is unsupported.", 409, retryable=False)
    keyframes = _validated_motion_keyframes(motion, frame_count=frame_count)
    fit_mode = str(motion.get("fit_mode", "cover"))
    anchor_x = _normalized_anchor(motion.get("anchor_x", 0.5), field="motion.anchor_x")
    anchor_y = _normalized_anchor(motion.get("anchor_y", 0.5), field="motion.anchor_y")

    # perspective's per-frame source counter is ``in`` and begins at zero,
    # matching the Phase 13 frame authority exactly.
    x_expr = _piecewise_expression(keyframes, "x", variable="in")
    y_expr = _piecewise_expression(keyframes, "y", variable="in")
    w_expr = _piecewise_expression(keyframes, "width", variable="in")
    h_expr = _piecewise_expression(keyframes, "height", variable="in")
    rect_x = f"(({x_expr})*W)"
    rect_y = f"(({y_expr})*H)"
    rect_w = f"(({w_expr})*W)"
    rect_h = f"(({h_expr})*H)"

    if fit_mode == "cover":
        # Select the largest output-aspect viewport contained by the exact
        # Phase 13 rectangle.  Anchors distribute only the rectangle's true
        # residual dimension; x/y remain absolute source-frame origins.
        #
        # Keep the four corner expressions in direct algebraic form.  Building
        # x1/y2 from recursively expanded viewport_x/viewport_w expressions
        # duplicates the same piecewise keyframe expression many times and can
        # push an otherwise ordinary 120-beat render above the 1 MB compiler
        # bound.  These branches are algebraically equivalent to the viewport
        # construction and retain the same source-bound clamping.
        is_wide = f"gte(({rect_w})*{height},({rect_h})*{width})"
        wide_w = f"({rect_h})*{width}/{height}"
        narrow_h = f"({rect_w})*{height}/{width}"
        wide_x = f"({rect_x})+{_format_number(anchor_x)}*(({rect_w})-({wide_w}))"
        narrow_y = f"({rect_y})+{_format_number(anchor_y)}*(({rect_h})-({narrow_h}))"

        wide_x0 = f"max(0,min(W-({wide_w}),({wide_x})))"
        narrow_x0 = f"max(0,min(W-({rect_w}),({rect_x})))"
        wide_x1 = f"max(({wide_w}),min(W,({wide_x})+({wide_w})))"
        narrow_x1 = f"max(({rect_w}),min(W,({rect_x})+({rect_w})))"
        wide_y0 = f"max(0,min(H-({rect_h}),({rect_y})))"
        narrow_y0 = f"max(0,min(H-({narrow_h}),({narrow_y})))"
        wide_y2 = f"max(({rect_h}),min(H,({rect_y})+({rect_h})))"
        narrow_y2 = f"max(({narrow_h}),min(H,({narrow_y})+({narrow_h})))"

        x0 = f"if({is_wide},{wide_x0},{narrow_x0})"
        y0 = f"if({is_wide},{wide_y0},{narrow_y0})"
        x1 = f"if({is_wide},{wide_x1},{narrow_x1})"
        y1 = y0
        x2 = x0
        y2 = f"if({is_wide},{wide_y2},{narrow_y2})"
        x3 = x1
        y3 = y2
        fit_tail = f"scale={width}:{height}:flags=bicubic"
    elif fit_mode == "contain":
        # The exact rectangle is mapped to the full fixed-size perspective
        # canvas, then its original aspect ratio is restored into a contained
        # output rectangle and positioned by the frozen anchor.  Current Phase
        # 13 contain motions retain a constant width/height ratio; reject a
        # materially varying ratio rather than silently distorting authority.
        ratio = float(keyframes[0]["width"]) / float(keyframes[0]["height"])
        for item in keyframes[1:]:
            candidate = float(item["width"]) / float(item["height"])
            if not math.isclose(candidate, ratio, rel_tol=0.0, abs_tol=1e-9):
                raise AppError(
                    "RENDER_MOTION_CONTAIN_ASPECT_INVALID",
                    "Contain motion crop aspect must remain constant across keyframes.",
                    409,
                    retryable=False,
                )
        viewport_x = rect_x
        viewport_y = rect_y
        viewport_w = rect_w
        viewport_h = rect_h
        crop_aspect = f"({_format_number(ratio)}*iw/ih)"
        is_wide = f"gte(({crop_aspect})*{height},{width})"
        contained_w = f"if({is_wide},{width},{height}*({crop_aspect}))"
        contained_h = f"if({is_wide},{width}/({crop_aspect}),{height})"
        fit_tail = (
            f"scale=w='max(2,trunc(({contained_w})/2)*2)':h='max(2,trunc(({contained_h})/2)*2)':flags=bicubic,"
            f"pad={width}:{height}:"
            f"x='max(0,min(ow-iw,{_format_number(anchor_x)}*(ow-iw)))':"
            f"y='max(0,min(oh-ih,{_format_number(anchor_y)}*(oh-ih)))':color=black"
        )
        x0 = viewport_x
        y0 = viewport_y
        x1 = f"({viewport_x})+({viewport_w})"
        y1 = viewport_y
        x2 = viewport_x
        y2 = f"({viewport_y})+({viewport_h})"
        x3 = x1
        y3 = y2
    else:
        raise AppError("RENDER_MOTION_INVALID", "Motion fit mode is invalid.", 409, retryable=False)

    perspective = (
        f"perspective=x0='{x0}':y0='{y0}':x1='{x1}':y1='{y1}':"
        f"x2='{x2}':y2='{y2}':x3='{x3}':y3='{y3}':"
        "sense=source:eval=frame:interpolation=cubic"
    )
    result = f"{perspective},{fit_tail}"
    if len(result.encode("utf-8")) > MAX_FILTER_SCRIPT_BYTES:
        raise AppError("RENDER_FILTER_SCRIPT_TOO_LARGE", "Motion filter script exceeds the compiler bound.", 413, retryable=False)
    return result


def compile_endpoint_filter(state: Any, *, width: int, height: int) -> str:
    if not isinstance(state, dict):
        raise AppError("RENDER_TRANSITION_INPUT_INVALID", "Transition endpoint state is missing.", 409, retryable=False)
    crop = _normalized_crop(state.get("crop"), field="transition_endpoint_state.crop")
    motion = {
        "fit_mode": str(state.get("fit_mode", "")),
        "anchor_x": _normalized_anchor(state.get("anchor_x"), field="transition_endpoint_state.anchor_x"),
        "anchor_y": _normalized_anchor(state.get("anchor_y"), field="transition_endpoint_state.anchor_y"),
        "interpolation": "piecewise_linear_by_frame_v1",
        "motion_filter_version": MOTION_FILTER_VERSION,
        "normalized_crop_version": NORMALIZED_CROP_VERSION,
        "keyframes": [{"frame": 0, **crop}],
    }
    return compile_motion_filter(motion, frame_count=1, width=width, height=height)


def _build_audio_filter_lines(graph: dict[str, Any], input_index_by_artifact: dict[str, int], *, final_label: str) -> list[str]:
    narration = graph["audio_nodes"]["narration"]
    narration_index = input_index_by_artifact[narration["artifact_id"]]
    total_seconds = narration["duration_time_units"] / 1_000_000
    lines = [f"[{narration_index}:a]aresample=48000,pan=stereo|c0=c0|c1=c0,atrim=duration={total_seconds:.6f},asetpts=PTS-STARTPTS[narr]"]
    audio_labels = ["narr"]
    background = graph["audio_nodes"].get("background_music")
    if background:
        idx = input_index_by_artifact[background["artifact_id"]]
        duration = (background["end_time_units"] - background["start_time_units"]) / 1_000_000
        source_start = background["source_start_time_units"] / 1_000_000
        delay_ms = max(0, int(round(background["start_time_units"] / 1000)))
        filters = ["aresample=48000", "pan=stereo|c0=c0|c1=c0", f"atrim=start={source_start:.6f}:duration={duration:.6f}", "asetpts=PTS-STARTPTS", _volume(float(background["gain_db"]))]
        fade_in = background.get("fade_in_time_units", 0) / 1_000_000
        fade_out = background.get("fade_out_time_units", 0) / 1_000_000
        if fade_in > 0:
            filters.append(f"afade=t=in:st=0:d={fade_in:.6f}")
        if fade_out > 0:
            filters.append(f"afade=t=out:st={max(0.0, duration-fade_out):.6f}:d={fade_out:.6f}")
        if delay_ms:
            filters.append(f"adelay={delay_ms}|{delay_ms}")
        lines.append(f"[{idx}:a]" + ",".join(filters) + "[music]")
        audio_labels.append("music")
    for sfx_index, sfx in enumerate(graph["audio_nodes"].get("sound_effects") or []):
        idx = input_index_by_artifact[sfx["artifact_id"]]
        label = f"sfx{sfx_index}"
        source_start = sfx["source_start_time_units"] / 1_000_000
        duration = sfx["duration_time_units"] / 1_000_000
        delay_ms = max(0, int(round(sfx["start_time_units"] / 1000)))
        lines.append(
            f"[{idx}:a]aresample=48000,pan=stereo|c0=c0|c1=c0,atrim=start={source_start:.6f}:duration={duration:.6f},"
            f"asetpts=PTS-STARTPTS,{_volume(float(sfx['gain_db']))},adelay={delay_ms}|{delay_ms}[{label}]"
        )
        audio_labels.append(label)
    if len(audio_labels) == 1:
        lines.append(f"[{audio_labels[0]}]atrim=duration={total_seconds:.6f},asetpts=PTS-STARTPTS[{final_label}]")
    else:
        lines.append("".join(f"[{item}]" for item in audio_labels) + f"amix=inputs={len(audio_labels)}:normalize=0:duration=longest,atrim=duration={total_seconds:.6f},asetpts=PTS-STARTPTS[{final_label}]")
    return lines


def build_filter_script(graph: dict[str, Any], *, input_index_by_artifact: dict[str, int]) -> str:
    profile = graph["profile"]
    fps_num = int(profile["frame_rate"]["numerator"])
    fps_den = int(profile["frame_rate"]["denominator"])
    fps = f"{fps_num}/{fps_den}"
    width, height = int(profile["width"]), int(profile["height"])
    visual_nodes = graph["visual_nodes"]

    usage: dict[str, int] = {}
    for node in visual_nodes:
        if node["node_type"] == "visual_clip":
            usage[node["artifact_id"]] = usage.get(node["artifact_id"], 0) + 1
        else:
            for key in ("previous_visual_artifact_id", "next_visual_artifact_id"):
                artifact_id = node[key]
                usage[artifact_id] = usage.get(artifact_id, 0) + 1
    source_labels: dict[str, list[str]] = {}
    lines: list[str] = []
    for artifact_order, artifact_id in enumerate(sorted(usage, key=lambda item: input_index_by_artifact[item])):
        count = usage[artifact_id]
        labels = [f"vsrc{artifact_order}_{index}" for index in range(count)]
        source_labels[artifact_id] = labels
        lines.append(f"[{input_index_by_artifact[artifact_id]}:v]split={count}" + "".join(f"[{label}]" for label in labels))
    used: dict[str, int] = {artifact_id: 0 for artifact_id in usage}

    def take(artifact_id: str) -> str:
        index = used[artifact_id]
        used[artifact_id] += 1
        return source_labels[artifact_id][index]

    clip_labels: list[str] = []
    padded_frame_indices: list[int] = []
    padded_frame_cursor = 0
    for index, node in enumerate(visual_nodes):
        output = f"clip{index}"
        clip_labels.append(output)
        frames = int(node["frame_count"])
        common_tail = f"fps={fps},setsar=1,format={profile['pixel_format']},trim=end_frame={frames},settb=AVTB,setpts=N/({fps}*TB),tpad=stop_mode=clone:stop=1"
        if node["node_type"] == "visual_clip":
            source = take(node["artifact_id"])
            motion_filter = compile_motion_filter(node["motion"], frame_count=frames, width=width, height=height)
            lines.append(f"[{source}]{motion_filter},{common_tail}[{output}]")
        else:
            left = take(node["previous_visual_artifact_id"])
            right = take(node["next_visual_artifact_id"])
            left_label, right_label = f"transition{index}a", f"transition{index}b"
            left_filter = compile_endpoint_filter(node["previous_state"], width=width, height=height)
            right_filter = compile_endpoint_filter(node["next_state"], width=width, height=height)
            prep_tail = f"fps={fps},setsar=1,format={profile['pixel_format']},trim=end_frame={frames},settb=AVTB,setpts=N/({fps}*TB)"
            lines.append(f"[{left}]{left_filter},{prep_tail}[{left_label}]")
            lines.append(f"[{right}]{right_filter},{prep_tail}[{right_label}]")
            if node["transition_type"] == "crossfade":
                alpha = "1" if frames == 1 else f"min(max((N-1)/{frames - 1},0),1)"
                lines.append(f"[{left_label}][{right_label}]blend=all_expr='A*(1-({alpha}))+B*({alpha})':shortest=1,fps={fps},trim=end_frame={frames},settb=AVTB,setpts=N/({fps}*TB),tpad=stop_mode=clone:stop=1[{output}]")
            else:
                lines.append(f"[{right_label}]trim=end_frame={frames},settb=AVTB,setpts=N/({fps}*TB),tpad=stop_mode=clone:stop=1[{output}]")
        padded_frame_cursor += frames + 1
        padded_frame_indices.append(padded_frame_cursor - 1)
    lines.append("".join(f"[{label}]" for label in clip_labels) + f"concat=n={len(clip_labels)}:v=1:a=0[vconcat]")
    drop_expression = "+".join(f"eq(n,{frame_index})" for frame_index in padded_frame_indices)
    lines.append(f"[vconcat]select='not({drop_expression})',settb=AVTB,setpts=N/({fps}*TB)[vout]")
    lines.extend(_build_audio_filter_lines(graph, input_index_by_artifact, final_label="aout"))
    script = ";\n".join(lines) + ";\n"
    encoded = script.encode("utf-8")
    if len(encoded) > MAX_FILTER_SCRIPT_BYTES:
        raise AppError("RENDER_FILTER_SCRIPT_TOO_LARGE", "Generated filter script exceeds the compiler bound.", 413, retryable=False)
    if "\\" in script or "../" in script:
        raise AppError("RENDER_FILTER_SCRIPT_INVALID", "Generated filter script contains forbidden path syntax.", 500, retryable=False)
    return script


def _materialize_windows_input_aliases(
    *,
    descriptors: list[dict[str, Any]],
    artifact_paths: dict[str, Path],
    alias_directory: Path,
) -> dict[str, Path]:
    """Create short, attempt-owned hard links for an oversized Windows argv.

    FFmpeg needs one ``-i`` argument per visual input. A production assembly can
    therefore exceed CreateProcess' fixed command-line limit even after paths
    are made relative to the artifact root. The attempt directory is on the
    same artifact volume as every validated input, so hard links provide short
    names without copying or mutating immutable media bytes.
    """

    alias_directory.mkdir(parents=False, exist_ok=False)
    aliases: dict[str, Path] = {}
    try:
        for actual_index, descriptor in enumerate(descriptors):
            artifact_id = str(descriptor["artifact_id"])
            source = artifact_paths[artifact_id]
            suffix = source.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                suffix = ".bin"
            alias = alias_directory / f"i{actual_index:04d}{suffix}"
            os.link(source, alias)
            aliases[artifact_id] = alias
    except OSError as exc:
        for alias in aliases.values():
            try:
                alias.unlink()
            except OSError:
                pass
        try:
            alias_directory.rmdir()
        except OSError:
            pass
        raise AppError(
            "RENDER_INPUT_ALIAS_FAILED",
            "Render could not prepare short Windows input paths.",
            422,
            retryable=True,
            details={
                "os_error_code": exc.errno,
                "winerror": getattr(exc, "winerror", None),
            },
        ) from exc
    return aliases


def build_ffmpeg_args(*, graph: dict[str, Any], artifact_paths: dict[str, Path], filter_script_path: Path, output_path: Path, settings: Settings | None = None) -> tuple[list[str], dict[str, int]]:
    runtime = settings or get_settings()
    profile = graph["profile"]
    fps_numerator = int(profile["frame_rate"]["numerator"])
    fps_denominator = int(profile["frame_rate"]["denominator"])
    fps = f"{fps_numerator}/{fps_denominator}"
    descriptors = sorted(graph["ordered_input_descriptors"], key=lambda item: int(item["input_index"]))
    args = [runtime.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats", "-progress", "pipe:1", "-y"]
    index_map: dict[str, int] = {}
    input_arg_positions: list[tuple[int, str]] = []
    for actual_index, descriptor in enumerate(descriptors):
        artifact_id = descriptor["artifact_id"]
        path = artifact_paths[artifact_id]
        index_map[artifact_id] = actual_index
        if descriptor["kind"] == "visual":
            args += ["-loop", "1", "-framerate", fps, "-i"]
        elif descriptor["kind"] == "background_music" and descriptor.get("loop_mode") == "loop":
            args += ["-stream_loop", "-1", "-i"]
        else:
            args += ["-i"]
        input_arg_positions.append((len(args), artifact_id))
        args.append(str(path))
    filter_script_path.write_text(build_filter_script(graph, input_index_by_artifact=index_map), encoding="utf-8", newline="\n")
    args += ["-filter_complex_script", str(filter_script_path), "-map", "[vout]", "-map", "[aout]"]
    subtitle = graph["subtitle_mapping"]
    if subtitle["mode"] == "embedded_mov_text":
        args += ["-map", f"{index_map[subtitle['srt_artifact_id']]}:0", "-c:s", "mov_text", "-metadata:s:s:0", "language=und"]
    args += [
        "-c:v", "libx264", "-preset", str(profile["video_preset"]), "-crf", str(profile["video_crf"]),
        "-pix_fmt", str(profile["pixel_format"]), "-r", fps, "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", f"{profile['audio_bitrate_kbps']}k", "-ar", "48000", "-ac", "2",
        "-threads", str(profile.get("encoder_threads", 1)), "-map_metadata", "-1", "-metadata", "creation_time=1970-01-01T00:00:00Z",
        "-movflags", "+faststart+use_metadata_tags", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        "-f", "mp4", str(output_path),
    ]
    if os.name == "nt" and _windows_command_line_chars(args) >= WINDOWS_RENDER_COMMAND_SAFETY_LIMIT:
        aliases = _materialize_windows_input_aliases(
            descriptors=descriptors,
            artifact_paths=artifact_paths,
            alias_directory=filter_script_path.parent / WINDOWS_INPUT_ALIAS_DIRECTORY,
        )
        for position, artifact_id in input_arg_positions:
            args[position] = str(aliases[artifact_id])
    return args, index_map


def build_audio_analysis_args(*, graph: dict[str, Any], artifact_paths: dict[str, Path], filter_script_path: Path, settings: Settings | None = None) -> list[str]:
    runtime = settings or get_settings()
    descriptors = [item for item in sorted(graph["ordered_input_descriptors"], key=lambda item: int(item["input_index"])) if item["kind"] in {"narration", "background_music", "sound_effect"}]
    args = [runtime.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "info", "-nostats", "-y"]
    index_map: dict[str, int] = {}
    for actual_index, descriptor in enumerate(descriptors):
        artifact_id = descriptor["artifact_id"]
        index_map[artifact_id] = actual_index
        if descriptor["kind"] == "background_music" and descriptor.get("loop_mode") == "loop":
            args += ["-stream_loop", "-1", "-i", str(artifact_paths[artifact_id])]
        else:
            args += ["-i", str(artifact_paths[artifact_id])]
    lines = _build_audio_filter_lines(graph, index_map, final_label="mix_for_analysis")
    lines.append("[mix_for_analysis]astats=metadata=1:reset=0[analysis]")
    filter_script_path.write_text(";\n".join(lines) + ";\n", encoding="utf-8", newline="\n")
    args += ["-filter_complex_script", str(filter_script_path), "-map", "[analysis]", "-f", "null", "-"]
    return args


class _BoundedTextBuffer:
    def __init__(self, maximum_bytes: int) -> None:
        self.maximum_bytes = max(1, int(maximum_bytes))
        self._items: deque[tuple[str, int]] = deque()
        self._size = 0
        self._lock = threading.Lock()

    def append(self, value: str) -> None:
        encoded_size = len(value.encode("utf-8", "replace"))
        if encoded_size > self.maximum_bytes:
            value = value.encode("utf-8", "replace")[-self.maximum_bytes:].decode("utf-8", "replace")
            encoded_size = len(value.encode("utf-8", "replace"))
        with self._lock:
            self._items.append((value, encoded_size))
            self._size += encoded_size
            while self._items and self._size > self.maximum_bytes:
                _, removed = self._items.popleft()
                self._size -= removed

    def text(self) -> str:
        with self._lock:
            return "".join(item for item, _ in self._items)

    def tail_lines(self, limit: int) -> list[str]:
        lines = self.text().splitlines()
        return lines[-limit:]


def _deadline_from_timeout(*, operation_deadline_monotonic: float | None, timeout_seconds: int | float | None) -> float:
    if operation_deadline_monotonic is not None:
        return float(operation_deadline_monotonic)
    if timeout_seconds is None or float(timeout_seconds) <= 0:
        raise AppError("RENDER_TIMEOUT", "The Render process deadline is invalid.", 504, retryable=True)
    return time.monotonic() + float(timeout_seconds)


def run_owned_process(
    args: list[str],
    *,
    operation_deadline_monotonic: float,
    cancellation_requested: Callable[[], bool],
    process_kind: str,
    cancel_grace_seconds: int,
    progress_line_callback: Callable[[str], None] | None = None,
    started_callback: Callable[[int], None] | None = None,
    finished_callback: Callable[[], None] | None = None,
    stdout_limit_bytes: int = PROCESS_STDOUT_LIMIT_BYTES,
    stderr_limit_bytes: int = PROCESS_STDERR_LIMIT_BYTES,
) -> dict[str, Any]:
    """Run one operation-owned process under cancellation and one deadline.

    Reader threads own only pipe reads and bounded buffers.  The caller-owned
    control loop remains responsive even when the child emits no output.
    """
    if cancellation_requested():
        raise AppError("RENDER_CANCELLED", "Render operation was cancelled.", 409, retryable=False)
    if time.monotonic() >= operation_deadline_monotonic:
        raise AppError("RENDER_TIMEOUT", "Local Render processing exceeded the configured overall timeout.", 504, retryable=True)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    stdout_buffer = _BoundedTextBuffer(stdout_limit_bytes)
    stderr_buffer = _BoundedTextBuffer(stderr_limit_bytes)
    stdout_queue: queue.Queue[str | None] = queue.Queue(maxsize=PROGRESS_QUEUE_MAX_RECORDS)
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            shell=False,
            stdin=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
            creationflags=creationflags,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C", "LC_ALL": "C"},
        )
    except OSError as exc:
        raise AppError(
            "RENDER_PROCESS_START_FAILED",
            "A local Render process could not be started.",
            422,
            retryable=True,
            details={
                "process_kind": process_kind,
                "os_error_code": exc.errno,
                "winerror": getattr(exc, "winerror", None),
            },
        ) from exc
    if started_callback:
        started_callback(process.pid)

    def consume_stdout() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                bounded = line[:8192]
                stdout_buffer.append(bounded)
                _put_bounded(stdout_queue, bounded)
        finally:
            _put_bounded(stdout_queue, None)

    def consume_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_buffer.append(line[:8192])

    stdout_thread = threading.Thread(target=consume_stdout, name=f"render-{process_kind}-stdout-{process.pid}")
    stderr_thread = threading.Thread(target=consume_stderr, name=f"render-{process_kind}-stderr-{process.pid}")
    stdout_thread.start()
    stderr_thread.start()
    started = time.monotonic()
    try:
        while True:
            if cancellation_requested():
                terminate_owned_process(process, grace_seconds=cancel_grace_seconds)
                raise AppError("RENDER_CANCELLED", "Render operation was cancelled.", 409, retryable=False, details={"process_kind": process_kind})
            if time.monotonic() >= operation_deadline_monotonic:
                terminate_owned_process(process, grace_seconds=cancel_grace_seconds)
                raise AppError("RENDER_TIMEOUT", "Local Render processing exceeded the configured overall timeout.", 504, retryable=True, details={"process_kind": process_kind})
            try:
                line = stdout_queue.get(timeout=PROGRESS_POLL_SECONDS)
            except queue.Empty:
                line = ""
            if line and progress_line_callback is not None:
                progress_line_callback(line)
            if process.poll() is not None and not stdout_thread.is_alive() and not stderr_thread.is_alive() and stdout_queue.empty():
                break
        return_code = process.wait(timeout=max(1, cancel_grace_seconds))
        return {
            "process_kind": process_kind,
            "return_code": return_code,
            "stdout": stdout_buffer.text(),
            "stderr": stderr_buffer.text(),
            "stdout_tail": stdout_buffer.tail_lines(20),
            "stderr_tail": stderr_buffer.tail_lines(20),
            "elapsed_seconds": time.monotonic() - started,
            "remaining_seconds": max(0.0, operation_deadline_monotonic - time.monotonic()),
        }
    finally:
        if process.poll() is None:
            terminate_owned_process(process, grace_seconds=cancel_grace_seconds)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        stdout_thread.join(timeout=max(1, cancel_grace_seconds + 1))
        stderr_thread.join(timeout=max(1, cancel_grace_seconds + 1))
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            raise AppError("RENDER_PROCESS_READER_LEAK", "Render process reader thread did not terminate.", 500, retryable=True, details={"process_kind": process_kind})
        if finished_callback is not None:
            finished_callback()


def analyze_audio_clipping(
    *,
    graph: dict[str, Any],
    artifact_paths: dict[str, Path],
    filter_script_path: Path,
    timeout_seconds: int | float | None = None,
    operation_deadline_monotonic: float | None = None,
    cancel_grace_seconds: int = 5,
    cancellation_requested: Callable[[], bool] | None = None,
    started_callback: Callable[[int], None] | None = None,
    finished_callback: Callable[[], None] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    profile = graph.get("profile") or {}
    if profile.get("clipping_policy") != "fail_on_detected_clipping":
        raise AppError("RENDER_CLIPPING_POLICY_INVALID", "Render profile clipping policy is unsupported.", 409, retryable=False)
    args = build_audio_analysis_args(graph=graph, artifact_paths=artifact_paths, filter_script_path=filter_script_path, settings=settings)
    deadline = _deadline_from_timeout(operation_deadline_monotonic=operation_deadline_monotonic, timeout_seconds=timeout_seconds)
    try:
        completed = run_owned_process(
            args,
            operation_deadline_monotonic=deadline,
            cancellation_requested=cancellation_requested or (lambda: False),
            process_kind="clipping_analysis",
            cancel_grace_seconds=cancel_grace_seconds,
            started_callback=started_callback,
            finished_callback=finished_callback,
        )
    except AppError as exc:
        if exc.error_code in {"RENDER_CANCELLED", "RENDER_TIMEOUT"}:
            raise
        raise AppError("RENDER_AUDIO_ANALYSIS_FAILED", "Render audio clipping analysis failed.", 422, retryable=False) from exc
    if completed["return_code"] != 0:
        raise AppError("RENDER_AUDIO_ANALYSIS_FAILED", "Render audio clipping analysis failed.", 422, retryable=False, details={"return_code": completed["return_code"]})
    peaks: list[float] = []
    for match in _PEAK_RE.finditer(completed["stderr"]):
        raw = match.group(1)
        try:
            value = float(raw)
        except ValueError as exc:
            raise AppError("RENDER_AUDIO_ANALYSIS_FAILED", "Render audio peak result is malformed.", 422, retryable=False) from exc
        if not math.isfinite(value):
            raise AppError("RENDER_AUDIO_ANALYSIS_FAILED", "Render audio peak result is not finite.", 422, retryable=False)
        peaks.append(value)
    if not peaks:
        raise AppError("RENDER_AUDIO_ANALYSIS_FAILED", "Render audio analysis did not report a peak level.", 422, retryable=False)
    peak = max(peaks)
    result = {
        "analyzer_version": AUDIO_CLIPPING_ANALYZER_VERSION,
        "clipping_policy": "fail_on_detected_clipping",
        "threshold_dbfs": CLIPPING_THRESHOLD_DBFS,
        "peak_level_dbfs": peak,
        "clipping_detected": peak >= CLIPPING_THRESHOLD_DBFS,
        "analysis_elapsed_seconds": completed["elapsed_seconds"],
    }
    if result["clipping_detected"]:
        raise AppError("RENDER_AUDIO_CLIPPING_DETECTED", "The exact Render audio mix clips at or above 0 dBFS.", 422, retryable=False, details=result)
    return result


def parse_progress_line(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    key, value = line.rstrip("\r\n").split("=", 1)
    if key not in PROGRESS_KEYS:
        return None
    return key, value.strip()


def terminate_owned_process(process: subprocess.Popen[str], *, grace_seconds: int) -> None:
    if process.poll() is not None:
        process.wait(timeout=max(1, grace_seconds))
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=max(1, grace_seconds))
        return
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=max(1, grace_seconds))


def _put_bounded(record_queue: queue.Queue[str | None], item: str | None) -> None:
    while True:
        try:
            record_queue.put_nowait(item)
            return
        except queue.Full:
            try:
                record_queue.get_nowait()
            except queue.Empty:
                return


def run_ffmpeg_process(
    args: list[str],
    *,
    total_duration_time_units: int,
    total_frames: int,
    timeout_seconds: int | float | None = None,
    operation_deadline_monotonic: float | None = None,
    cancel_grace_seconds: int,
    progress_callback: Callable[[dict[str, Any]], None],
    cancellation_requested: Callable[[], bool],
    started_callback: Callable[[int], None] | None = None,
    finished_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    deadline = _deadline_from_timeout(operation_deadline_monotonic=operation_deadline_monotonic, timeout_seconds=timeout_seconds)
    current: dict[str, Any] = {"frame": 0, "out_time_us": 0, "speed": None, "progress": "continue"}

    def consume_progress(line: str) -> None:
        parsed = parse_progress_line(line)
        if not parsed:
            return
        key, value = parsed
        if key in {"frame", "out_time_us"}:
            try:
                current[key] = max(0, int(value))
            except ValueError:
                return
        else:
            current[key] = value
        percent_time = int(current["out_time_us"] * 98 / max(1, total_duration_time_units))
        percent_frame = int(current["frame"] * 98 / max(1, total_frames))
        current["percent"] = max(0, min(98, max(percent_time, percent_frame)))
        progress_callback(dict(current))

    try:
        result = run_owned_process(
            args,
            operation_deadline_monotonic=deadline,
            cancellation_requested=cancellation_requested,
            process_kind="final_encode",
            cancel_grace_seconds=cancel_grace_seconds,
            progress_line_callback=consume_progress,
            started_callback=started_callback,
            finished_callback=finished_callback,
        )
    except AppError as exc:
        if exc.error_code in {"RENDER_CANCELLED", "RENDER_TIMEOUT", "RENDER_PROCESS_READER_LEAK"}:
            raise
        cause_details = {
            key: exc.details[key]
            for key in ("process_kind", "os_error_code", "winerror")
            if key in exc.details
        }
        raise AppError(
            "RENDER_FFMPEG_FAILED",
            "Local FFmpeg rendering failed.",
            422,
            retryable=True,
            details={"cause_error_code": exc.error_code, **cause_details},
        ) from exc
    if result["return_code"] != 0:
        tail = result["stderr_tail"]
        path_tokens = sorted({arg for arg in args if isinstance(arg, str) and (arg.startswith("/") or (len(arg) > 2 and arg[1:3] == ":\\"))}, key=len, reverse=True)
        sanitized_tail = []
        for line in tail:
            safe = line
            for token in path_tokens:
                safe = safe.replace(token, "<controlled-path>")
            sanitized_tail.append(safe)
        raise AppError("RENDER_FFMPEG_FAILED", "Local FFmpeg rendering failed.", 422, retryable=True, details={"return_code": result["return_code"], "stderr_tail": sanitized_tail})
    return {
        "return_code": result["return_code"],
        "stderr_tail": result["stderr_tail"],
        "stdout_tail": result["stdout_tail"],
        "elapsed_seconds": result["elapsed_seconds"],
        "remaining_seconds": result["remaining_seconds"],
    }
