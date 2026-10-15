from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from typing import Any

from app.api.errors import AppError

FRAME_QUANTIZER_VERSION = "render_frame_quantizer_v1"
TIME_UNITS_PER_SECOND = 1_000_000


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_json_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _round_fraction_half_up(value: Fraction) -> int:
    return (value.numerator * 2 + value.denominator) // (2 * value.denominator)


def quantize_render_frames(
    *,
    total_duration_time_units: int,
    beats: list[dict[str, Any]],
    frame_rate_numerator: int,
    frame_rate_denominator: int,
) -> dict[str, Any]:
    if total_duration_time_units <= 0 or frame_rate_numerator <= 0 or frame_rate_denominator <= 0:
        raise AppError("RENDER_FRAME_QUANTIZATION_INVALID", "Render frame quantization input is invalid.", 409, retryable=False)
    if not beats:
        raise AppError("RENDER_FRAME_QUANTIZATION_INVALID", "At least one Assembly beat is required.", 409, retryable=False)
    ordered = sorted(beats, key=lambda item: int(item["beat_order"]))
    if ordered != beats or int(ordered[0]["start_time_units"]) != 0 or int(ordered[-1]["end_time_units"]) != total_duration_time_units:
        raise AppError("RENDER_FRAME_QUANTIZATION_INVALID", "Assembly beat timing is not a contiguous ordered timeline.", 409, retryable=False)
    for previous, current in zip(ordered, ordered[1:]):
        if int(previous["end_time_units"]) != int(current["start_time_units"]):
            raise AppError("RENDER_FRAME_QUANTIZATION_INVALID", "Assembly beat timing contains a gap or overlap.", 409, retryable=False)

    fps = Fraction(frame_rate_numerator, frame_rate_denominator)
    total_frames = _round_fraction_half_up(Fraction(total_duration_time_units * frame_rate_numerator, TIME_UNITS_PER_SECOND * frame_rate_denominator))
    if total_frames < len(ordered):
        raise AppError(
            "RENDER_FRAME_QUANTIZATION_INFEASIBLE",
            "The selected frame rate cannot represent every Assembly interval with at least one frame.",
            409,
            retryable=False,
            details={"beat_count": len(ordered), "total_frame_count": total_frames},
        )

    intervals: list[dict[str, Any]] = []
    previous_end = 0
    max_boundary_error = 0
    for index, beat in enumerate(ordered):
        remaining = len(ordered) - index - 1
        source_end = int(beat["end_time_units"])
        if index == len(ordered) - 1:
            end_frame = total_frames
        else:
            ideal_boundary = Fraction(source_end * frame_rate_numerator, TIME_UNITS_PER_SECOND * frame_rate_denominator)
            rounded = _round_fraction_half_up(ideal_boundary)
            end_frame = max(previous_end + 1, min(rounded, total_frames - remaining))
        start_frame = previous_end
        frame_count = end_frame - start_frame
        if frame_count < 1:
            raise AppError("RENDER_FRAME_QUANTIZATION_INFEASIBLE", "An Assembly interval cannot receive a positive frame allocation.", 409, retryable=False)
        quantized_boundary_units = Fraction(end_frame * TIME_UNITS_PER_SECOND * frame_rate_denominator, frame_rate_numerator)
        boundary_error = _round_fraction_half_up(abs(quantized_boundary_units - source_end))
        max_boundary_error = max(max_boundary_error, boundary_error)
        intervals.append({
            "scene_id": str(beat["scene_id"]),
            "beat_id": str(beat["beat_id"]),
            "beat_order": int(beat["beat_order"]),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "frame_count": frame_count,
            "source_start_time_units": int(beat["start_time_units"]),
            "source_end_time_units": source_end,
            "boundary_error_time_units": boundary_error,
        })
        previous_end = end_frame

    frame_duration = Fraction(TIME_UNITS_PER_SECOND * frame_rate_denominator, frame_rate_numerator)
    if max_boundary_error >= _round_fraction_half_up(frame_duration) + 1:
        raise AppError("RENDER_FRAME_QUANTIZATION_INFEASIBLE", "A frame boundary drift exceeded one output frame.", 409, retryable=False)
    quantized_duration_units = _round_fraction_half_up(Fraction(total_frames * TIME_UNITS_PER_SECOND * frame_rate_denominator, frame_rate_numerator))
    payload = {
        "schema_version": "3.0",
        "quantizer_version": FRAME_QUANTIZER_VERSION,
        "frame_rate": {"numerator": frame_rate_numerator, "denominator": frame_rate_denominator},
        "source_total_duration_time_units": total_duration_time_units,
        "total_frame_count": total_frames,
        "quantized_duration_time_units": quantized_duration_units,
        "total_drift_time_units": quantized_duration_units - total_duration_time_units,
        "max_boundary_error_time_units": max_boundary_error,
        "intervals": intervals,
    }
    payload["frame_quantization_sha256"] = canonical_json_sha256(payload)
    return payload
