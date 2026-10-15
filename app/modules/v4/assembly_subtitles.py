from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, TypedDict

from app.api.errors import AppError

CUE_COMPILER_VERSION = "subtitle_cue_compiler_v1"
MILLISECOND_QUANTIZER_VERSION = "subtitle_millisecond_quantizer_v1"
SRT_SERIALIZER_VERSION = "subtitle_srt_serializer_v1"
VTT_SERIALIZER_VERSION = "subtitle_vtt_serializer_v1"
UNITS_PER_SECOND = 1_000_000
UNITS_PER_MILLISECOND = 1_000
MAX_LINE = 42
MAX_CUE_CHARS = 84
MAX_CUE_UNITS = 6 * UNITS_PER_SECOND


class QuantizedSubtitleCue(TypedDict):
    cue_id: str
    cue_order: int
    start_ms: int
    end_ms: int
    line_1: str
    line_2: str | None
    exact_text: str
    source_start: int
    source_end: int


def seconds_to_units(value: float | str) -> int:
    return int((Decimal(str(value)) * UNITS_PER_SECOND).to_integral_value(rounding=ROUND_HALF_UP))


def units_to_seconds(value: int) -> float:
    return float(Decimal(value) / Decimal(UNITS_PER_SECOND))


def canonical_json_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _wrap_exact(text: str) -> tuple[str, str | None, bool]:
    if len(text) <= MAX_LINE:
        return text, None, False
    break_at = text.rfind(" ", 0, MAX_LINE + 1)
    if break_at <= 0:
        # Preserve an overlong token intact.
        token_end = text.find(" ")
        if token_end < 0:
            return text, None, True
        break_at = token_end
    line1 = text[:break_at].rstrip()
    line2 = text[break_at:].lstrip()
    if len(line2) > MAX_LINE:
        return line1, line2, True
    return line1, line2, False


def samples_to_units(sample: int, sample_rate_hz: int) -> int:
    if sample < 0 or sample_rate_hz <= 0:
        raise AppError("ASSEMBLY_SUBTITLE_TIMING_INVALID", "Subtitle sample timing is invalid.", 422, retryable=False)
    return int(sample) * UNITS_PER_SECOND // int(sample_rate_hz)


def _flatten_word_timing(word_timing_map: dict[str, Any]) -> list[dict[str, Any]]:
    if word_timing_map.get("schema_version") != "narration_word_timing_map_v1":
        raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "Assembly requires NarrationWordTimingMap v1.", 409, retryable=False)
    sample_rate = int(word_timing_map.get("sample_rate_hz") or 0)
    total_samples = int(word_timing_map.get("total_sample_count") or 0)
    sections = word_timing_map.get("sections")
    if sample_rate != 48_000 or total_samples <= 0 or not isinstance(sections, list) or not sections:
        raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "NarrationWordTimingMap timing authority is malformed.", 409, retryable=False)
    words: list[dict[str, Any]] = []
    previous_sample_start = -1
    previous_sample_end = -1
    previous_source_start = -1
    previous_source_end = -1
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("section_id"), str):
            raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "NarrationWordTimingMap section identity is malformed.", 409, retryable=False)
        section_id = str(section["section_id"])
        section_words = section.get("words")
        if not isinstance(section_words, list):
            raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "NarrationWordTimingMap section words are malformed.", 409, retryable=False)
        for raw in section_words:
            if not isinstance(raw, dict):
                raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "NarrationWordTimingMap word is malformed.", 409, retryable=False)
            required = ("section_word_index", "authored_start", "authored_end", "global_start_sample", "global_end_sample")
            if any(not isinstance(raw.get(field), int) or isinstance(raw.get(field), bool) for field in required):
                raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "NarrationWordTimingMap word lacks exact global sample lineage.", 409, retryable=False)
            source_start = int(raw["authored_start"])
            source_end = int(raw["authored_end"])
            sample_start = int(raw["global_start_sample"])
            sample_end = int(raw["global_end_sample"])
            if source_start < 0 or source_end <= source_start or sample_start < 0 or sample_end <= sample_start or sample_end > total_samples:
                raise AppError(
                    "ASSEMBLY_WORD_TIMING_INVALID",
                    "NarrationWordTimingMap word range is invalid.",
                    409,
                    retryable=False,
                    details={
                        "section_id": section_id,
                        "section_word_index": int(raw["section_word_index"]),
                        "authored_start": source_start,
                        "authored_end": source_end,
                        "global_start_sample": sample_start,
                        "global_end_sample": sample_end,
                        "total_sample_count": total_samples,
                    },
                )
            if sample_start < previous_sample_start or sample_end < previous_sample_end:
                raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "NarrationWordTimingMap word samples are not monotonic.", 409, retryable=False)
            same_authored_owner = source_start == previous_source_start and source_end == previous_source_end
            if source_start < previous_source_start or (previous_source_end >= 0 and source_start < previous_source_end and not same_authored_owner):
                raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "NarrationWordTimingMap authored ownership overlaps unexpectedly.", 409, retryable=False)
            words.append({
                "word_id": f"{section_id}:word:{int(raw['section_word_index'])}",
                "source_start": source_start,
                "source_end": source_end,
                "sample_start": sample_start,
                "sample_end": sample_end,
            })
            previous_sample_start = sample_start
            previous_sample_end = sample_end
            previous_source_start = source_start
            previous_source_end = source_end
    if not words:
        raise AppError("ASSEMBLY_WORD_TIMING_INVALID", "NarrationWordTimingMap contains no project-owned words.", 409, retryable=False)
    return words


def _associations(start: int, end: int, beat_timing_map: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    beats = beat_timing_map.get("beats")
    if not isinstance(beats, list) or not beats:
        raise AppError("ASSEMBLY_BEAT_TIMING_INVALID", "BeatTimingMap contains no Beats.", 409, retryable=False)
    overlapping = [
        beat for beat in beats
        if isinstance(beat, dict)
        and int(beat.get("source_end", -1)) > start
        and int(beat.get("source_start", -1)) < end
    ]
    if not overlapping:
        raise AppError("ASSEMBLY_SUBTITLE_ASSOCIATION_INVALID", "Subtitle cue has no BeatTimingMap association.", 409, retryable=False)
    scene_ids = list(dict.fromkeys(str(beat["scene_id"]) for beat in overlapping))
    beat_ids = [str(beat["beat_id"]) for beat in overlapping]
    return [], scene_ids, beat_ids


def compile_subtitle_cues(
    *,
    full_script: str,
    word_timing_map: dict[str, Any],
    beat_timing_map: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    words = _flatten_word_timing(word_timing_map)
    sample_rate = int(word_timing_map["sample_rate_hz"])
    cues: list[dict[str, Any]] = []
    warnings: list[str] = []
    cursor = 0
    while cursor < len(words):
        start_index = cursor
        first = words[cursor]
        last = first
        cursor += 1
        while cursor < len(words):
            candidate = words[cursor]
            same_authored_owner = candidate["source_start"] == last["source_start"] and candidate["source_end"] == last["source_end"]
            candidate_end = max(int(last["source_end"]), int(candidate["source_end"]))
            candidate_text = full_script[int(first["source_start"]):candidate_end]
            candidate_units = samples_to_units(int(candidate["sample_end"]), sample_rate) - samples_to_units(int(first["sample_start"]), sample_rate)
            current_text = full_script[int(first["source_start"]):int(last["source_end"])]
            current_ends_sentence = current_text.rstrip().endswith((".", "!", "?"))
            if not same_authored_owner and current_ends_sentence and len(current_text) >= 18:
                break
            if not same_authored_owner and (len(candidate_text) > MAX_CUE_CHARS or candidate_units > MAX_CUE_UNITS):
                break
            last = candidate
            cursor += 1
            if not same_authored_owner and candidate_text.rstrip().endswith((".", "!", "?")):
                break
            if not same_authored_owner and candidate_text.rstrip().endswith((",", ";", ":")) and len(candidate_text) >= 42:
                break
        selected = words[start_index:cursor]
        first, last = selected[0], selected[-1]
        start = int(first["source_start"])
        end = max(int(word["source_end"]) for word in selected)
        exact = full_script[start:end]
        if not exact:
            raise AppError("ASSEMBLY_SUBTITLE_TEXT_INVALID", "Subtitle authored text is empty.", 409, retryable=False)
        line1, line2, over = _wrap_exact(exact)
        if over:
            warnings.append(f"cue_{len(cues)+1:04d}:over_limit_exact_text_preserved")
        start_units = samples_to_units(int(first["sample_start"]), sample_rate)
        end_units = samples_to_units(int(last["sample_end"]), sample_rate)
        if cues and start_units < cues[-1]["end_time_units"]:
            start_units = cues[-1]["end_time_units"]
        if end_units <= start_units:
            raise AppError("ASSEMBLY_SUBTITLE_TIMING_INVALID", "Subtitle cue timing is not positive.", 409, retryable=False)
        segment_ids, scene_ids, beat_ids = _associations(start, end, beat_timing_map)
        cue_id = f"cue_{len(cues)+1:04d}"
        cues.append({
            "cue_id": cue_id, "cue_order": len(cues)+1, "source_start": start, "source_end": end,
            "exact_text": exact, "start_time_units": start_units, "end_time_units": end_units,
            "duration_time_units": end_units-start_units, "start_seconds": units_to_seconds(start_units),
            "end_seconds": units_to_seconds(end_units), "duration_seconds": units_to_seconds(end_units-start_units),
            "line_1": line1, "line_2": line2, "word_ids": [w["word_id"] for w in selected],
            "segment_ids": segment_ids, "scene_ids": scene_ids, "beat_ids": beat_ids,
        })
    flattened = [wid for cue in cues for wid in cue["word_ids"]]
    expected = [word["word_id"] for word in words]
    if flattened != expected:
        raise AppError("ASSEMBLY_SUBTITLE_WORD_COVERAGE_INVALID", "Subtitle cues do not cover every NarrationWordTimingMap word exactly once.", 409, retryable=False)
    return cues, warnings

def _quantization_error(*, cue_count: int, total_duration_units: int, cue_id: str | None = None) -> AppError:
    details: dict[str, Any] = {
        "cue_count": cue_count,
        "total_duration_time_units": total_duration_units,
        "serialized_timeline_end_ms": math.ceil(total_duration_units / UNITS_PER_MILLISECOND),
        "quantizer_version": MILLISECOND_QUANTIZER_VERSION,
    }
    if cue_id is not None:
        details["cue_id"] = cue_id
    return AppError(
        "SUBTITLE_MILLISECOND_QUANTIZATION_INFEASIBLE",
        "Subtitle cues cannot be represented as positive, ordered millisecond intervals inside the narration timeline.",
        422,
        retryable=False,
        details=details,
    )


def quantize_subtitle_cues(
    cues: list[dict[str, Any]],
    *,
    total_duration_units: int,
) -> list[QuantizedSubtitleCue]:
    """Create one deterministic millisecond cue model shared by SRT and VTT."""

    if total_duration_units <= 0:
        raise _quantization_error(cue_count=len(cues), total_duration_units=total_duration_units)
    timeline_end_ms = math.ceil(total_duration_units / UNITS_PER_MILLISECOND)
    if not cues or len(cues) > timeline_end_ms:
        raise _quantization_error(cue_count=len(cues), total_duration_units=total_duration_units)

    result: list[QuantizedSubtitleCue] = []
    previous_source_end = -1
    previous_end_ms = 0
    for index, cue in enumerate(cues):
        cue_id = str(cue.get("cue_id", f"cue_{index+1:04d}"))
        cue_order = int(cue.get("cue_order", index + 1))
        start_units = int(cue["start_time_units"])
        end_units = int(cue["end_time_units"])
        source_start = int(cue.get("source_start", 0))
        source_end = int(cue.get("source_end", source_start + 1))
        if (
            cue_order != index + 1
            or start_units < 0
            or end_units <= start_units
            or end_units > total_duration_units
            or source_end <= source_start
            or (index and start_units < int(cues[index - 1]["end_time_units"]))
            or source_start < previous_source_end
        ):
            raise _quantization_error(cue_count=len(cues), total_duration_units=total_duration_units, cue_id=cue_id)

        raw_start_ms = start_units // UNITS_PER_MILLISECOND
        raw_end_ms = math.ceil(end_units / UNITS_PER_MILLISECOND)
        remaining = len(cues) - index - 1
        latest_end_ms = timeline_end_ms - remaining
        start_ms = max(raw_start_ms, previous_end_ms)
        if start_ms + 1 > latest_end_ms:
            raise _quantization_error(cue_count=len(cues), total_duration_units=total_duration_units, cue_id=cue_id)
        end_ms = max(raw_end_ms, start_ms + 1)
        # Reserve one positive millisecond for each remaining cue.  Clamping is
        # deterministic and never extends the final cue beyond narration.
        end_ms = min(end_ms, latest_end_ms)
        if end_ms <= start_ms:
            raise _quantization_error(cue_count=len(cues), total_duration_units=total_duration_units, cue_id=cue_id)

        line_1 = str(cue["line_1"])
        line_2_value = cue.get("line_2")
        line_2 = str(line_2_value) if line_2_value is not None else None
        exact_text = str(cue["exact_text"])
        quantized: QuantizedSubtitleCue = {
            "cue_id": cue_id,
            "cue_order": cue_order,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "line_1": line_1,
            "line_2": line_2,
            "exact_text": exact_text,
            "source_start": source_start,
            "source_end": source_end,
        }
        result.append(quantized)
        previous_end_ms = end_ms
        previous_source_end = source_end

    if result[-1]["end_ms"] > timeline_end_ms:
        raise _quantization_error(cue_count=len(cues), total_duration_units=total_duration_units, cue_id=result[-1]["cue_id"])
    return result


def quantized_cue_model_sha256(cues: list[QuantizedSubtitleCue]) -> str:
    return canonical_json_hash(cues)


def _coerce_quantized(
    cues: list[dict[str, Any]],
    *,
    total_duration_units: int | None,
) -> list[QuantizedSubtitleCue]:
    if cues and all("start_ms" in cue and "end_ms" in cue for cue in cues):
        normalized: list[QuantizedSubtitleCue] = []
        previous_end = 0
        for index, cue in enumerate(cues):
            start_ms = int(cue["start_ms"])
            end_ms = int(cue["end_ms"])
            if start_ms < previous_end or end_ms <= start_ms:
                raise _quantization_error(
                    cue_count=len(cues),
                    total_duration_units=(total_duration_units if total_duration_units is not None else max(end_ms, 1) * UNITS_PER_MILLISECOND),
                    cue_id=str(cue.get("cue_id", f"cue_{index+1:04d}")),
                )
            normalized.append({
                "cue_id": str(cue.get("cue_id", f"cue_{index+1:04d}")),
                "cue_order": int(cue.get("cue_order", index + 1)),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "line_1": str(cue["line_1"]),
                "line_2": str(cue["line_2"]) if cue.get("line_2") is not None else None,
                "exact_text": str(cue.get("exact_text", cue["line_1"])),
                "source_start": int(cue.get("source_start", 0)),
                "source_end": int(cue.get("source_end", 1)),
            })
            previous_end = end_ms
        if total_duration_units is not None and normalized and normalized[-1]["end_ms"] > math.ceil(total_duration_units / UNITS_PER_MILLISECOND):
            raise _quantization_error(cue_count=len(cues), total_duration_units=total_duration_units, cue_id=normalized[-1]["cue_id"])
        return normalized
    if total_duration_units is None:
        if not cues:
            raise _quantization_error(cue_count=0, total_duration_units=0)
        total_duration_units = max(int(cue["end_time_units"]) for cue in cues)
    return quantize_subtitle_cues(cues, total_duration_units=total_duration_units)


def _timestamp_ms(millis: int, *, comma: bool) -> str:
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    sep = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{ms:03d}"


def serialize_srt(cues: list[dict[str, Any]], *, total_duration_units: int | None = None) -> bytes:
    quantized = _coerce_quantized(cues, total_duration_units=total_duration_units)
    blocks: list[str] = []
    for index, cue in enumerate(quantized, 1):
        text = cue["line_1"] + (("\n" + cue["line_2"]) if cue.get("line_2") else "")
        blocks.append(f"{index}\n{_timestamp_ms(cue['start_ms'], comma=True)} --> {_timestamp_ms(cue['end_ms'], comma=True)}\n{text}")
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def serialize_vtt(cues: list[dict[str, Any]], *, total_duration_units: int | None = None) -> bytes:
    quantized = _coerce_quantized(cues, total_duration_units=total_duration_units)
    blocks = ["WEBVTT"]
    for cue in quantized:
        text = cue["line_1"] + (("\n" + cue["line_2"]) if cue.get("line_2") else "")
        blocks.append(f"{_timestamp_ms(cue['start_ms'], comma=False)} --> {_timestamp_ms(cue['end_ms'], comma=False)}\n{text}")
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


_SRT_TIMING_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2,}):(\d{2}):(\d{2}),(\d{3})$")
_VTT_TIMING_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})\.(\d{3}) --> (\d{2,}):(\d{2}):(\d{2})\.(\d{3})$")


def _parts_to_ms(parts: tuple[str, str, str, str]) -> int:
    hours, minutes, seconds, millis = (int(value) for value in parts)
    if minutes >= 60 or seconds >= 60:
        raise ValueError("invalid subtitle timestamp")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _parse_timing_pairs(raw: bytes, *, vtt: bool) -> list[tuple[int, int]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise AppError("ASSEMBLY_SUBTITLE_SERIALIZATION_INVALID", "Subtitle output is not valid UTF-8.", 422, retryable=False) from exc
    matcher = _VTT_TIMING_RE if vtt else _SRT_TIMING_RE
    pairs: list[tuple[int, int]] = []
    for line in lines:
        match = matcher.match(line)
        if not match:
            continue
        values = match.groups()
        pairs.append((_parts_to_ms(values[:4]), _parts_to_ms(values[4:])))
    return pairs


def validate_serialized_subtitles(
    *,
    srt_bytes: bytes,
    vtt_bytes: bytes,
    quantized_cues: list[QuantizedSubtitleCue],
    total_duration_units: int,
) -> None:
    srt_pairs = _parse_timing_pairs(srt_bytes, vtt=False)
    vtt_pairs = _parse_timing_pairs(vtt_bytes, vtt=True)
    expected_pairs = [(cue["start_ms"], cue["end_ms"]) for cue in quantized_cues]
    if srt_pairs != expected_pairs or vtt_pairs != expected_pairs or srt_pairs != vtt_pairs:
        raise AppError(
            "ASSEMBLY_SUBTITLE_SERIALIZATION_INVALID",
            "SRT and VTT serialized boundaries do not match the shared quantized cue model.",
            422,
            retryable=False,
        )
    previous_end = 0
    for start_ms, end_ms in expected_pairs:
        if start_ms < previous_end or end_ms <= start_ms:
            raise AppError(
                "ASSEMBLY_SUBTITLE_SERIALIZATION_INVALID",
                "Serialized subtitle cues overlap or have zero duration.",
                422,
                retryable=False,
            )
        previous_end = end_ms
    timeline_end_ms = math.ceil(total_duration_units / UNITS_PER_MILLISECOND)
    if expected_pairs and expected_pairs[-1][1] > timeline_end_ms:
        raise AppError(
            "ASSEMBLY_SUBTITLE_SERIALIZATION_INVALID",
            "Serialized subtitle cues exceed the narration timeline.",
            422,
            retryable=False,
        )
