from __future__ import annotations

import math
import re
from typing import Any

from app.api.errors import AppError

ALIGNMENT_VERSION = "voice_alignment_disabled_until_task3"
_WORD = re.compile(r"[^\W_]+(?:[’'][^\W_]+)?", flags=re.UNICODE)


def _char_weight(ch: str) -> int:
    if ch.isspace():
        return 1
    if ch in ".!?":
        return 6
    if ch in ",;:":
        return 4
    if ch in "—–-":
        return 3
    return 3


def _integer_boundaries(text: str, sample_start: int, sample_end: int) -> list[int]:
    if sample_end <= sample_start or not text:
        raise AppError("VOICE_ALIGNMENT_INVALID", "Alignment input must contain text and positive audio samples.", 422, retryable=False)
    weights = [_char_weight(ch) for ch in text]
    total_weight = sum(weights)
    total_samples = sample_end - sample_start
    cumulative = 0
    boundaries = [sample_start]
    for weight in weights[:-1]:
        cumulative += weight
        boundaries.append(sample_start + (total_samples * cumulative // total_weight))
    boundaries.append(sample_end)
    return boundaries


def fallback_segment_alignment(*, exact_text: str, source_start: int, sample_start: int, sample_end: int) -> dict[str, Any]:
    _ = (exact_text, source_start, sample_start, sample_end)
    raise AppError(
        "VOICE_ALIGNMENT_NOT_READY",
        "Legacy fallback alignment is disabled; Task 3 must use ElevenLabs Forced Alignment.",
        409,
        retryable=False,
    )


def validate_provider_word_timings(*, exact_text: str, duration_seconds: float, timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(timings, list) or not timings:
        raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider timing is empty.", 422, retryable=False)
    normalized: list[dict[str, Any]] = []
    cursor = 0.0
    coverage = []
    for item in timings:
        text = item.get("text")
        start = item.get("start_seconds")
        end = item.get("end_seconds")
        if not isinstance(text, str) or not text or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider timing fields are invalid.", 422, retryable=False)
        start_f, end_f = float(start), float(end)
        if not math.isfinite(start_f) or not math.isfinite(end_f) or start_f < cursor or end_f < start_f or end_f > duration_seconds + 1e-9:
            raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider timing timestamps are invalid.", 422, retryable=False)
        pos = exact_text.find(text, coverage[-1][1] if coverage else 0)
        if pos < 0:
            raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider timing text does not match the exact narration.", 422, retryable=False)
        coverage.append((pos, pos + len(text)))
        normalized.append({"text": text, "local_source_start": pos, "local_source_end": pos + len(text), "start_seconds": start_f, "end_seconds": end_f})
        cursor = end_f
    visible = "".join(exact_text[a:b] for a, b in coverage)
    expected_visible = "".join(match.group(0) for match in _WORD.finditer(exact_text))
    if "".join(_WORD.findall(visible)) != expected_visible:
        raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider timing does not cover the exact narration words.", 422, retryable=False)
    return normalized



def validate_provider_character_timings(*, exact_text: str, duration_seconds: float, timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate exact provider character/grapheme coverage.

    Entries may contain one or more Unicode code points, but their concatenated
    text must equal the canonical segment byte-for-byte. Offsets are Python
    Unicode string offsets and remain exact within the current Script.
    """
    if not isinstance(timings, list) or not timings:
        raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider character timing is empty.", 422, retryable=False)
    normalized: list[dict[str, Any]] = []
    cursor_time = 0.0
    cursor_text = 0
    rebuilt: list[str] = []
    for item in timings:
        text = item.get("text")
        start = item.get("start_seconds")
        end = item.get("end_seconds")
        if not isinstance(text, str) or not text or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider character timing fields are invalid.", 422, retryable=False)
        start_f, end_f = float(start), float(end)
        if not math.isfinite(start_f) or not math.isfinite(end_f) or start_f < cursor_time or end_f < start_f or end_f > duration_seconds + 1e-9:
            raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider character timestamps are invalid.", 422, retryable=False)
        local_end = cursor_text + len(text)
        normalized.append({
            "text": text,
            "local_source_start": cursor_text,
            "local_source_end": local_end,
            "start_seconds": start_f,
            "end_seconds": end_f,
        })
        rebuilt.append(text)
        cursor_text = local_end
        cursor_time = end_f
    if "".join(rebuilt) != exact_text:
        raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider character timing does not exactly cover the narration.", 422, retryable=False)
    return normalized


def word_timings_from_provider_characters(*, exact_text: str, duration_seconds: float, timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    characters = validate_provider_character_timings(exact_text=exact_text, duration_seconds=duration_seconds, timings=timings)
    words: list[dict[str, Any]] = []
    for match in _WORD.finditer(exact_text):
        start_offset, end_offset = match.span()
        overlapping = [
            item for item in characters
            if item["local_source_end"] > start_offset and item["local_source_start"] < end_offset
        ]
        if not overlapping:
            raise AppError("VOICE_PROVIDER_TIMING_INVALID", "Provider character timing cannot be mapped to narration words.", 422, retryable=False)
        words.append({
            "text": match.group(0),
            "local_source_start": start_offset,
            "local_source_end": end_offset,
            "start_seconds": overlapping[0]["start_seconds"],
            "end_seconds": overlapping[-1]["end_seconds"],
        })
    if not words:
        words.append({
            "text": exact_text,
            "local_source_start": 0,
            "local_source_end": len(exact_text),
            "start_seconds": characters[0]["start_seconds"],
            "end_seconds": characters[-1]["end_seconds"],
        })
    return words

def sample_for_source_offset(*, source_offset: int, words: list[dict[str, Any]], default_start: int, default_end: int) -> int:
    if not words:
        return default_start
    for word in words:
        start, end = int(word["source_start"]), int(word["source_end"])
        if start <= source_offset <= end:
            if end == start:
                return int(word["sample_start"])
            span = int(word["sample_end"]) - int(word["sample_start"])
            return int(word["sample_start"]) + span * (source_offset - start) // (end - start)
        if source_offset < start:
            return int(word["sample_start"])
    return default_end
