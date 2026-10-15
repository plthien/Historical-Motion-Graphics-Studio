from __future__ import annotations

from typing import Any

from app.api.errors import AppError
from app.modules.v4.voice_first_policy import BEAT_TIMING_POLICY_VERSION

SAMPLE_RATE_HZ = 48_000
BEAT_TIMING_MAP_SCHEMA_VERSION = "beat_timing_map_v1"
_CLAUSE_TERMINALS = frozenset(".!?;")
_CLAUSE_CLOSERS = frozenset("\"'”’»）)]")
_ABBREVIATION_TOKENS = frozenset({
    "dr.",
    "mr.",
    "mrs.",
    "ms.",
    "st.",
})


def _is_nonterminal_abbreviation(
    narration: str,
    *,
    raw_start: int,
    terminal_end: int,
) -> bool:
    """Recognize a narrow full authored token that should not end a clause.

    Inspect the whole non-whitespace token containing the current period, not
    only the prefix ending at that period. This matters for initialisms such as
    U.S. and e.g.: B1 already forbids Scene cuts inside one timing atom, while
    this guard prevents the legal token-end boundary from being treated as a
    semantic sentence boundary merely because the token contains periods.
    """

    token_start = terminal_end - 1
    while token_start > raw_start and not narration[token_start - 1].isspace():
        token_start -= 1

    token_end = terminal_end
    while token_end < len(narration) and not narration[token_end].isspace():
        token_end += 1

    remainder = narration[token_end:]
    if not remainder.strip():
        return False

    token = narration[token_start:token_end].strip(
        "\"'“”‘’([{"
    )
    while token and token[-1] in _CLAUSE_CLOSERS:
        token = token[:-1]
    token = token.lower()

    if token in _ABBREVIATION_TOKENS:
        return True
    if not token.endswith("."):
        return False
    parts = token[:-1].split(".")
    return (
        len(parts) >= 2
        and all(len(part) == 1 and part.isalpha() for part in parts)
    )


def _natural_clause_spans(narration: str) -> list[tuple[int, int]]:
    """Return authored-text clause spans without mutating source characters.

    Natural boundaries are proposed after terminal punctuation/semicolon or
    immediately before a bare newline. Surrounding whitespace is excluded from
    the clause span itself so the next backend-owned Scene can retain it exactly.
    Closing quote/bracket delimiters stay attached to terminal punctuation.

    This function proposes authored-text boundaries only. Spoken-override safety
    is applied later by build_scene_timing_context() before micro-Scene planning.
    """

    spans: list[tuple[int, int]] = []
    segment_start = 0
    cursor = 0
    length = len(narration)

    def append_trimmed(raw_start: int, raw_end: int) -> None:
        start = raw_start
        end = raw_end
        while start < end and narration[start].isspace():
            start += 1
        while end > start and narration[end - 1].isspace():
            end -= 1
        if end > start:
            spans.append((start, end))

    while cursor < length:
        char = narration[cursor]

        if char in "\r\n":
            append_trimmed(segment_start, cursor)
            cursor += 1
            segment_start = cursor
            continue

        if char in _CLAUSE_TERMINALS:
            end = cursor + 1
            while end < length and narration[end] in _CLAUSE_TERMINALS:
                end += 1
            while end < length and narration[end] in _CLAUSE_CLOSERS:
                end += 1
            if (
                char == "."
                and _is_nonterminal_abbreviation(
                    narration,
                    raw_start=segment_start,
                    terminal_end=end,
                )
            ):
                cursor = end
                continue
            append_trimmed(segment_start, end)
            cursor = end
            segment_start = end
            continue

        cursor += 1

    append_trimmed(segment_start, length)
    return spans


def _sections(word_timing_map: dict[str, Any]) -> list[dict[str, Any]]:
    sections = word_timing_map.get("sections")
    if not isinstance(sections, list) or not sections or any(not isinstance(item, dict) for item in sections):
        raise AppError("BEAT_TIMING_MAP_INVALID", "NarrationWordTimingMap sections are missing.", 409, retryable=False)
    return sections


def validate_word_timing_authority(
    *,
    project_id: str,
    script_artifact_id: str,
    narration_audio_artifact_id: str,
    narration_word_timing_map_artifact_id: str,
    word_timing_map: dict[str, Any],
) -> None:
    del narration_word_timing_map_artifact_id
    expected = {
        "source_script_artifact_id": script_artifact_id,
        "source_narration_audio_artifact_id": narration_audio_artifact_id,
    }
    for field, value in expected.items():
        if word_timing_map.get(field) != value:
            raise AppError(
                "BEAT_TIMING_LINEAGE_STALE",
                "NarrationWordTimingMap lineage does not match the approved Voice pair.",
                409,
                retryable=False,
                details={"field": field},
            )
    if word_timing_map.get("schema_version") != "narration_word_timing_map_v1":
        raise AppError("BEAT_TIMING_MAP_INVALID", "NarrationWordTimingMap schema is unsupported.", 409, retryable=False)
    if int(word_timing_map.get("sample_rate_hz") or 0) != SAMPLE_RATE_HZ:
        raise AppError("BEAT_TIMING_MAP_INVALID", "NarrationWordTimingMap must use the 48kHz canonical sample rate.", 409, retryable=False)
    if int(word_timing_map.get("total_sample_count") or 0) <= 0:
        raise AppError("BEAT_TIMING_MAP_INVALID", "NarrationWordTimingMap total sample count is invalid.", 409, retryable=False)
    _sections(word_timing_map)
    _ordered_words(word_timing_map)


def _unsafe_override_boundary(word_timing_map: dict[str, Any], source_offset: int) -> bool:
    for section in _sections(word_timing_map):
        for mapping in section.get("authored_spoken_mapping", []):
            if not isinstance(mapping, dict):
                continue
            start = mapping.get("authored_start")
            end = mapping.get("authored_end")
            if isinstance(start, int) and isinstance(end, int) and start < source_offset < end:
                return True
    return False


def require_safe_boundary(word_timing_map: dict[str, Any], source_offset: int) -> None:
    if _unsafe_override_boundary(word_timing_map, source_offset):
        raise AppError(
            "BEAT_TIMING_BOUNDARY_UNSAFE",
            "Scene Beat boundary falls strictly inside a spoken-override authored span.",
            422,
            retryable=False,
            details={"source_offset": source_offset},
        )


def _ordered_words(word_timing_map: dict[str, Any]) -> list[dict[str, Any]]:
    total_sample_count = word_timing_map.get("total_sample_count")
    if (
        not isinstance(total_sample_count, int)
        or isinstance(total_sample_count, bool)
        or total_sample_count <= 0
    ):
        raise AppError("BEAT_TIMING_MAP_INVALID", "NarrationWordTimingMap total sample count is invalid.", 409, retryable=False)
    words: list[dict[str, Any]] = []
    previous_sample_start = -1
    previous_sample_end = -1
    for section in _sections(word_timing_map):
        section_id = str(section.get("section_id") or "")
        section_words = section.get("words")
        if not isinstance(section_words, list):
            raise AppError("BEAT_TIMING_MAP_INVALID", "Narration word timing is malformed.", 409, retryable=False)
        for fallback_index, word in enumerate(section_words):
            if not isinstance(word, dict):
                raise AppError("BEAT_TIMING_MAP_INVALID", "Narration word timing is malformed.", 409, retryable=False)
            required = ("authored_start", "authored_end", "global_start_sample", "global_end_sample")
            if any(
                not isinstance(word.get(field), int) or isinstance(word.get(field), bool)
                for field in required
            ):
                raise AppError("BEAT_TIMING_MAP_INVALID", "Narration word timing is malformed.", 409, retryable=False)
            authored_start = int(word["authored_start"])
            authored_end = int(word["authored_end"])
            sample_start = int(word["global_start_sample"])
            sample_end = int(word["global_end_sample"])
            details = {
                "section_id": section_id,
                "section_word_index": word.get("section_word_index", fallback_index),
                "authored_start": authored_start,
                "authored_end": authored_end,
                "global_start_sample": sample_start,
                "global_end_sample": sample_end,
                "total_sample_count": total_sample_count,
            }
            if (
                authored_start < 0
                or authored_end <= authored_start
                or sample_start < 0
                or sample_end <= sample_start
                or sample_end > total_sample_count
            ):
                raise AppError(
                    "BEAT_TIMING_MAP_INVALID",
                    "Narration project-owned word range is invalid.",
                    409,
                    retryable=False,
                    details=details,
                )
            if (
                sample_start < previous_sample_start
                or sample_end < previous_sample_end
            ):
                raise AppError(
                    "BEAT_TIMING_MAP_INVALID",
                    "Narration project-owned word timing is not monotonic.",
                    409,
                    retryable=False,
                    details=details,
                )
            words.append(word)
            previous_sample_start = sample_start
            previous_sample_end = sample_end
    words.sort(key=lambda item: (int(item["authored_start"]), int(item["global_start_sample"]), str(item.get("word_id") or "")))
    if not words:
        raise AppError("BEAT_TIMING_MAP_INVALID", "NarrationWordTimingMap contains no project-owned words.", 409, retryable=False)
    return words


def _timing_section_for_source(
    word_timing_map: dict[str, Any],
    source_offset: int,
) -> dict[str, Any]:
    matches = [
        section
        for section in _sections(word_timing_map)
        if int(section.get("authored_section_start", -1)) <= source_offset
        <= int(section.get("authored_section_end", -1))
    ]
    if len(matches) != 1:
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "Authored source boundary cannot be mapped to exactly one Voice section.",
            422,
            retryable=False,
            details={"source_offset": source_offset},
        )
    return matches[0]


def _authored_boundary_to_spoken(
    timing: dict[str, Any],
    source_offset: int,
) -> int:
    section_start = int(timing.get("authored_section_start") or 0)
    section_end = int(timing.get("authored_section_end") or 0)
    if not section_start <= source_offset <= section_end:
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "Authored boundary is outside its Voice section.",
            422,
            retryable=False,
            details={"source_offset": source_offset},
        )

    authored_cursor = section_start
    spoken_cursor = 0
    mappings = [
        item
        for item in timing.get("authored_spoken_mapping", [])
        if isinstance(item, dict)
    ]
    mappings.sort(key=lambda item: (int(item.get("authored_start", -1)), int(item.get("authored_end", -1))))
    for mapping in mappings:
        authored_start = int(mapping["authored_start"])
        authored_end = int(mapping["authored_end"])
        spoken_start = int(mapping["spoken_start"])
        spoken_end = int(mapping["spoken_end"])
        if source_offset <= authored_start:
            return spoken_cursor + (source_offset - authored_cursor)
        if authored_start < source_offset < authored_end:
            raise AppError(
                "BEAT_TIMING_BOUNDARY_UNSAFE",
                "Authored boundary falls strictly inside an indivisible spoken override.",
                422,
                retryable=False,
                details={"source_offset": source_offset},
            )
        if source_offset == authored_end:
            return spoken_end
        authored_cursor = authored_end
        spoken_cursor = spoken_end
    return spoken_cursor + (source_offset - authored_cursor)


def _sample_for_spoken_offset(
    timing: dict[str, Any],
    spoken_offset: int,
    *,
    fallback: int,
) -> int:
    character_timing = [
        item
        for item in timing.get("character_timing", [])
        if isinstance(item, dict)
    ]
    for character in character_timing:
        spoken_start = character.get("spoken_start")
        spoken_end = character.get("spoken_end")
        char_start = character.get("global_start_sample")
        char_end = character.get("global_end_sample")
        if (
            not isinstance(spoken_start, int)
            or not isinstance(spoken_end, int)
            or not isinstance(char_start, int)
            or not isinstance(char_end, int)
            or spoken_end < spoken_start
            or char_end < char_start
        ):
            continue
        if spoken_offset == spoken_start:
            return char_start
        if spoken_offset == spoken_end:
            return char_end
        if spoken_start < spoken_offset < spoken_end:
            width = spoken_end - spoken_start
            return (
                char_start
                if width <= 0
                else char_start
                + round(
                    (char_end - char_start)
                    * (spoken_offset - spoken_start)
                    / width
                )
            )

    for word in timing.get("words", []):
        if not isinstance(word, dict):
            continue
        word_spoken_start = word.get("spoken_start")
        word_spoken_end = word.get("spoken_end")
        word_sample_start = word.get("global_start_sample")
        word_sample_end = word.get("global_end_sample")
        if (
            not isinstance(word_spoken_start, int)
            or not isinstance(word_spoken_end, int)
            or not isinstance(word_sample_start, int)
            or not isinstance(word_sample_end, int)
            or word_spoken_end <= word_spoken_start
            or word_sample_end <= word_sample_start
        ):
            continue
        if spoken_offset <= word_spoken_start:
            if spoken_offset == word_spoken_start:
                return word_sample_start
            continue
        if spoken_offset >= word_spoken_end:
            if spoken_offset == word_spoken_end:
                return word_sample_end
            continue
        return word_sample_start + round(
            (word_sample_end - word_sample_start)
            * (spoken_offset - word_spoken_start)
            / (word_spoken_end - word_spoken_start)
        )
    return fallback


def _sample_for_authored_boundary(
    word_timing_map: dict[str, Any],
    source_offset: int,
    *,
    fallback: int,
) -> int:
    timing = _timing_section_for_source(word_timing_map, source_offset)
    spoken_offset = _authored_boundary_to_spoken(timing, source_offset)
    return _sample_for_spoken_offset(
        timing,
        spoken_offset,
        fallback=fallback,
    )


def _speech_range(
    word_timing_map: dict[str, Any],
    words: list[dict[str, Any]],
    source_start: int,
    source_end: int,
) -> tuple[int, int]:
    matches = [
        word
        for word in words
        if int(word["authored_end"]) > source_start
        and int(word["authored_start"]) < source_end
    ]
    if not matches:
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "Scene Beat Script span cannot be mapped to NarrationWordTimingMap.",
            422,
            retryable=False,
            details={"source_start": source_start, "source_end": source_end},
        )

    first = matches[0]
    last = matches[-1]
    start = int(first["global_start_sample"])
    end = int(last["global_end_sample"])

    if int(first["authored_start"]) < source_start < int(first["authored_end"]):
        start = _sample_for_authored_boundary(
            word_timing_map,
            source_start,
            fallback=start,
        )
    if int(last["authored_start"]) < source_end < int(last["authored_end"]):
        end = _sample_for_authored_boundary(
            word_timing_map,
            source_end,
            fallback=end,
        )

    if start < 0 or end < start:
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "Narration speech sample range is invalid.",
            422,
            retryable=False,
            details={"source_start": source_start, "source_end": source_end},
        )
    return start, end



def project_visual_intervals_for_source_spans(
    word_timing_map: dict[str, Any],
    source_spans: list[list[int]] | list[tuple[int, int]],
) -> list[dict[str, int]]:
    """Canonical approved-Voice projection for ordered Script spans.

    This is the single visual-interval policy used by Scene planning, timing
    diagnostics, and final BeatTimingMap compilation:

    - first Beat of the whole narration starts at sample 0;
    - every later Beat starts at its own first spoken sample;
    - every Beat ends at the next selected Beat's visual start;
    - the last selected span ends at the next project word when one exists,
      otherwise at the narration master total sample count.

    Callers may project a whole plan, one section, one micro-Scene, or one
    candidate span. Because the calculation uses project-global authored and
    sample coordinates, Section boundaries never create a timing reset.
    """
    if not isinstance(source_spans, list) or not source_spans:
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "At least one ordered Script span is required.",
            422,
            retryable=False,
        )

    words = _ordered_words(word_timing_map)
    total_samples = int(word_timing_map.get("total_sample_count") or 0)
    sample_rate = int(word_timing_map.get("sample_rate_hz") or 0)
    if total_samples <= 0 or sample_rate <= 0:
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "NarrationWordTimingMap master timing is invalid.",
            409,
            retryable=False,
        )

    spans: list[tuple[int, int]] = []
    previous_end = -1
    for raw in source_spans:
        if (
            not isinstance(raw, (list, tuple))
            or len(raw) != 2
            or not all(isinstance(value, int) for value in raw)
        ):
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Scene Beat Script span is invalid.",
                422,
                retryable=False,
            )
        source_start, source_end = int(raw[0]), int(raw[1])
        if source_start < 0 or source_end <= source_start or source_start < previous_end:
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Scene Beat Script spans are not canonically ordered.",
                422,
                retryable=False,
            )
        require_safe_boundary(word_timing_map, source_start)
        require_safe_boundary(word_timing_map, source_end)
        spans.append((source_start, source_end))
        previous_end = source_end

    speech = [
        _speech_range(word_timing_map, words, start, end)
        for start, end in spans
    ]
    previous_speech_start = -1
    previous_speech_end = -1
    for speech_start, speech_end in speech:
        if (
            speech_start < previous_speech_start
            or speech_end < previous_speech_end
            or speech_end > total_samples
        ):
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Scene Beat speech samples are not canonically ordered.",
                422,
                retryable=False,
            )
        previous_speech_start = speech_start
        previous_speech_end = speech_end

    result: list[dict[str, int]] = []
    for index, ((source_start, source_end), (speech_start, speech_end)) in enumerate(
        zip(spans, speech, strict=True)
    ):
        first_authored_word_start = min(
            int(word["authored_start"])
            for word in words
        )
        has_prior_authored_content = source_start > first_authored_word_start
        visual_start = speech_start if has_prior_authored_content else 0

        if index + 1 < len(speech):
            visual_end = int(speech[index + 1][0])
        else:
            containing_word = next(
                (
                    word
                    for word in words
                    if int(word["authored_start"]) < source_end
                    < int(word["authored_end"])
                ),
                None,
            )
            if containing_word is not None:
                visual_end = _sample_for_authored_boundary(
                    word_timing_map,
                    source_end,
                    fallback=int(containing_word["global_end_sample"]),
                )
            else:
                next_project_word_start = next(
                    (
                        int(word["global_start_sample"])
                        for word in words
                        if int(word["authored_start"]) >= source_end
                    ),
                    None,
                )
                visual_end = (
                    total_samples
                    if next_project_word_start is None
                    else next_project_word_start
                )

        if visual_end <= visual_start:
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Projected Beat visual interval must be positive.",
                422,
                retryable=False,
                details={
                    "source_start": source_start,
                    "source_end": source_end,
                    "visual_start_sample": visual_start,
                    "visual_end_sample": visual_end,
                },
            )
        result.append(
            {
                "source_start": source_start,
                "source_end": source_end,
                "speech_start_sample": speech_start,
                "speech_end_sample": speech_end,
                "visual_start_sample": visual_start,
                "visual_end_sample": visual_end,
            }
        )
    return result

def compile_beat_timing_map(
    *,
    project_id: str,
    script_artifact_id: str,
    scene_plan_artifact_id: str,
    narration_audio_artifact_id: str,
    narration_word_timing_map_artifact_id: str,
    scene_plan: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> dict[str, Any]:
    validate_word_timing_authority(
        project_id=project_id,
        script_artifact_id=script_artifact_id,
        narration_audio_artifact_id=narration_audio_artifact_id,
        narration_word_timing_map_artifact_id=narration_word_timing_map_artifact_id,
        word_timing_map=word_timing_map,
    )
    if (
        scene_plan.get("project_id") != project_id
        or scene_plan.get("scene_plan_revision_id") != scene_plan_artifact_id
    ):
        raise AppError(
            "BEAT_TIMING_LINEAGE_STALE",
            "Scene Plan identity does not match BeatTimingMap compiler input.",
            409,
            retryable=False,
        )
    if scene_plan.get("source_script_revision_id") != script_artifact_id:
        raise AppError(
            "BEAT_TIMING_LINEAGE_STALE",
            "Scene Plan Script lineage is stale.",
            409,
            retryable=False,
        )
    scene_items = scene_plan.get("scenes")
    if not isinstance(scene_items, list) or not scene_items:
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "Approved Scene Plan contains no Scenes.",
            422,
            retryable=False,
        )

    compiled: list[dict[str, Any]] = []
    source_spans: list[list[int]] = []
    seen: set[str] = set()
    previous_source_end = -1
    for scene in scene_items:
        if (
            not isinstance(scene, dict)
            or not isinstance(scene.get("beats"), list)
            or not scene["beats"]
        ):
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Every Scene must contain at least one Beat.",
                422,
                retryable=False,
            )
        for beat in scene["beats"]:
            beat_id = str(beat.get("beat_id") or "") if isinstance(beat, dict) else ""
            if not beat_id or beat_id in seen:
                raise AppError(
                    "BEAT_TIMING_MAP_INVALID",
                    "Every Scene Beat must appear exactly once.",
                    422,
                    retryable=False,
                )
            seen.add(beat_id)
            span = beat.get("resolved_script_span")
            if (
                not isinstance(span, list)
                or len(span) != 2
                or not all(isinstance(value, int) for value in span)
            ):
                raise AppError(
                    "BEAT_TIMING_MAP_INVALID",
                    "Scene Beat Script span is invalid.",
                    422,
                    retryable=False,
                )
            source_start, source_end = int(span[0]), int(span[1])
            if (
                source_start < 0
                or source_end <= source_start
                or source_start < previous_source_end
            ):
                raise AppError(
                    "BEAT_TIMING_MAP_INVALID",
                    "Scene Beat Script spans are not canonically ordered.",
                    422,
                    retryable=False,
                )
            source_spans.append([source_start, source_end])
            compiled.append(
                {
                    "scene_id": str(scene["scene_id"]),
                    "scene_order": int(scene["scene_order"]),
                    "beat_id": beat_id,
                    "beat_order": int(beat["beat_order"]),
                    "local_beat_order": int(beat["local_beat_order"]),
                    "source_start": source_start,
                    "source_end": source_end,
                    "speech_start_sample": 0,
                    "speech_end_sample": 0,
                    "visual_start_sample": 0,
                    "visual_end_sample": 0,
                }
            )
            previous_source_end = source_end

    if not compiled:
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "Scene Plan contains no Beats.",
            422,
            retryable=False,
        )

    intervals = project_visual_intervals_for_source_spans(
        word_timing_map,
        source_spans,
    )
    for beat, interval in zip(compiled, intervals, strict=True):
        beat["speech_start_sample"] = int(interval["speech_start_sample"])
        beat["speech_end_sample"] = int(interval["speech_end_sample"])
        beat["visual_start_sample"] = int(interval["visual_start_sample"])
        beat["visual_end_sample"] = int(interval["visual_end_sample"])

    total_samples = int(word_timing_map["total_sample_count"])
    if (
        compiled[0]["visual_start_sample"] != 0
        or compiled[-1]["visual_end_sample"] != total_samples
    ):
        raise AppError(
            "BEAT_TIMING_MAP_INVALID",
            "Beat visual timeline does not cover the narration master timeline.",
            422,
            retryable=False,
        )
    for left, right in zip(compiled, compiled[1:]):
        if left["visual_end_sample"] != right["visual_start_sample"]:
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Beat visual timeline contains a gap or overlap.",
                422,
                retryable=False,
            )

    return {
        "artifact_type": "beat_timing_map",
        "schema_version": BEAT_TIMING_MAP_SCHEMA_VERSION,
        "project_id": project_id,
        "source_script_revision_id": script_artifact_id,
        "source_scene_plan_revision_id": scene_plan_artifact_id,
        "source_narration_audio_artifact_id": narration_audio_artifact_id,
        "source_narration_word_timing_map_artifact_id": narration_word_timing_map_artifact_id,
        "beat_timing_policy_version": BEAT_TIMING_POLICY_VERSION,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "total_narration_sample_count": total_samples,
        "beats": compiled,
    }

def _scene_timing_atoms(
    *,
    narration: str,
    authored_start: int,
    authored_end: int,
    timing: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, int]]]:
    section_words = [word for word in timing.get("words", []) if isinstance(word, dict)]
    if not section_words:
        raise AppError("BEAT_TIMING_MAP_INVALID", "Approved Voice section contains no project-owned word timing.", 409, retryable=False)

    raw_mappings = timing.get("authored_spoken_mapping", [])
    if not isinstance(raw_mappings, list):
        raise AppError("BEAT_TIMING_MAP_INVALID", "Narration spoken-override mapping is malformed.", 409, retryable=False)

    overrides: list[dict[str, int]] = []
    for mapping in raw_mappings:
        if not isinstance(mapping, dict):
            raise AppError("BEAT_TIMING_MAP_INVALID", "Narration spoken-override mapping is malformed.", 409, retryable=False)
        source_start = mapping.get("authored_start")
        source_end = mapping.get("authored_end")
        spoken_start = mapping.get("spoken_start")
        spoken_end = mapping.get("spoken_end")
        if not isinstance(spoken_start, int) and isinstance(source_start, int):
            spoken_start = source_start - authored_start
        if not isinstance(spoken_end, int) and isinstance(source_end, int):
            spoken_end = source_end - authored_start
        if (
            not isinstance(source_start, int)
            or not isinstance(source_end, int)
            or not isinstance(spoken_start, int)
            or not isinstance(spoken_end, int)
            or source_start < authored_start
            or source_end > authored_end
            or source_end <= source_start
            or spoken_end <= spoken_start
        ):
            raise AppError("BEAT_TIMING_MAP_INVALID", "Narration spoken-override mapping is outside its Script section.", 409, retryable=False)
        overrides.append({
            "source_start": source_start,
            "source_end": source_end,
            "spoken_start": spoken_start,
            "spoken_end": spoken_end,
        })
    overrides.sort(key=lambda item: (item["source_start"], item["source_end"]))
    previous_override_end = -1
    for override in overrides:
        if override["source_start"] < previous_override_end:
            raise AppError("BEAT_TIMING_MAP_INVALID", "Narration spoken-override mappings overlap.", 409, retryable=False)
        previous_override_end = override["source_end"]

    covered_by_override: dict[tuple[int, int], list[dict[str, Any]]] = {
        (item["source_start"], item["source_end"]): []
        for item in overrides
    }
    timing_atoms: list[dict[str, Any]] = []

    def add_word_atom(
        source_start: int,
        source_end: int,
        speech_start: int,
        speech_end: int,
    ) -> None:
        local_start = source_start - authored_start
        local_end = source_end - authored_start
        source_text = narration[local_start:local_end]
        if not source_text:
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Narration word timing cannot be projected to frozen Script text.",
                409,
                retryable=False,
            )
        timing_atoms.append({
            "kind": "word",
            "source_start": source_start,
            "source_end": source_end,
            "source_text": source_text,
            "indivisible": False,
            "start_boundary_safe": not _unsafe_override_boundary(
                word_timing_map, source_start
            ),
            "end_boundary_safe": not _unsafe_override_boundary(
                word_timing_map, source_end
            ),
            "speech_start_sample": speech_start,
            "speech_end_sample": speech_end,
        })

    def authored_sample(
        source_offset: int,
        *,
        fallback: int,
    ) -> int:
        return _sample_for_spoken_offset(
            timing,
            _authored_boundary_to_spoken(timing, source_offset),
            fallback=fallback,
        )

    for word in section_words:
        required = (
            "authored_start",
            "authored_end",
            "global_start_sample",
            "global_end_sample",
        )
        if (
            any(
                not isinstance(word.get(field), int) or isinstance(word.get(field), bool)
                for field in required
            )
            or not isinstance(word.get("text"), str)
            or not word.get("text")
        ):
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Narration word timing is malformed.",
                409,
                retryable=False,
            )
        source_start = int(word["authored_start"])
        source_end = int(word["authored_end"])
        speech_start = int(word["global_start_sample"])
        speech_end = int(word["global_end_sample"])
        if (
            source_start < authored_start
            or source_end > authored_end
            or source_end <= source_start
            or speech_end <= speech_start
        ):
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Narration word timing is outside its Script/Voice section.",
                409,
                retryable=False,
            )

        overlaps = [
            item
            for item in overrides
            if item["source_start"] < source_end
            and source_start < item["source_end"]
        ]
        if not overlaps:
            add_word_atom(source_start, source_end, speech_start, speech_end)
            continue

        # Project-owned Voice words inside a replacement phrase can legitimately
        # map back to the same complete authored override span. Treat those words
        # only as timing coverage for that one indivisible override atom. Emit
        # ordinary atoms solely for authored portions outside the override spans,
        # so no replacement audio is double-counted and no duplicate source
        # ownership is created.
        for override in overlaps:
            covered_by_override[
                (int(override["source_start"]), int(override["source_end"]))
            ].append(word)

        cursor = source_start
        for override in overlaps:
            overlap_start = max(source_start, int(override["source_start"]))
            overlap_end = min(source_end, int(override["source_end"]))
            if overlap_start >= overlap_end:
                continue

            if cursor < overlap_start:
                ordinary_start_sample = authored_sample(
                    cursor,
                    fallback=speech_start,
                )
                ordinary_end_sample = authored_sample(
                    overlap_start,
                    fallback=speech_end,
                )
                if ordinary_end_sample < ordinary_start_sample:
                    raise AppError(
                        "BEAT_TIMING_MAP_INVALID",
                        "Authored/spoken timing projection is not monotonic.",
                        409,
                        retryable=False,
                    )
                add_word_atom(
                    cursor,
                    overlap_start,
                    ordinary_start_sample,
                    ordinary_end_sample,
                )
            cursor = max(cursor, overlap_end)

        if cursor < source_end:
            ordinary_start_sample = authored_sample(
                cursor,
                fallback=speech_start,
            )
            ordinary_end_sample = authored_sample(
                source_end,
                fallback=speech_end,
            )
            if ordinary_end_sample < ordinary_start_sample:
                raise AppError(
                    "BEAT_TIMING_MAP_INVALID",
                    "Post-override timing projection is not monotonic.",
                    409,
                    retryable=False,
                )
            add_word_atom(
                cursor,
                source_end,
                ordinary_start_sample,
                ordinary_end_sample,
            )

    for override in overrides:
        key = (override["source_start"], override["source_end"])
        covered = sorted(
            covered_by_override[key],
            key=lambda word: (
                int(word["global_start_sample"]),
                int(word["global_end_sample"]),
            ),
        )
        if not covered:
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Spoken override has no project-owned word timing coverage.",
                409,
                retryable=False,
                details={"source_start": key[0], "source_end": key[1]},
            )
        local_start = key[0] - authored_start
        local_end = key[1] - authored_start
        source_text = narration[local_start:local_end]
        if not source_text:
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Spoken override cannot be projected to frozen Script text.",
                409,
                retryable=False,
            )

        fallback_start = min(
            int(word["global_start_sample"])
            for word in covered
        )
        fallback_end = max(
            int(word["global_end_sample"])
            for word in covered
        )
        override_sample_start = _sample_for_spoken_offset(
            timing,
            int(override["spoken_start"]),
            fallback=fallback_start,
        )
        override_sample_end = _sample_for_spoken_offset(
            timing,
            int(override["spoken_end"]),
            fallback=fallback_end,
        )
        if override_sample_end < override_sample_start:
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Spoken-override timing projection is not monotonic.",
                409,
                retryable=False,
            )
        timing_atoms.append({
            "kind": "spoken_override",
            "source_start": key[0],
            "source_end": key[1],
            "source_text": source_text,
            "indivisible": True,
            "start_boundary_safe": True,
            "end_boundary_safe": True,
            "speech_start_sample": override_sample_start,
            "speech_end_sample": override_sample_end,
        })

    timing_atoms.sort(
        key=lambda item: (
            int(item["source_start"]),
            int(item["source_end"]),
            int(item["speech_start_sample"]),
        )
    )
    if not timing_atoms:
        raise AppError("BEAT_TIMING_MAP_INVALID", "Approved Voice section contains no Scene timing atoms.", 409, retryable=False)

    previous_source_end = -1
    previous_speech_end = -1
    for atom in timing_atoms:
        speech_start = int(atom["speech_start_sample"])
        speech_end = int(atom["speech_end_sample"])
        if (
            int(atom["source_start"]) < previous_source_end
            or speech_start < previous_speech_end
            or speech_end < speech_start
        ):
            raise AppError(
                "BEAT_TIMING_MAP_INVALID",
                "Scene timing atoms overlap or are not canonically ordered.",
                409,
                retryable=False,
            )
        previous_source_end = int(atom["source_end"])
        previous_speech_end = speech_end

    public_override_spans = [
        {
            "source_start": int(override["source_start"]),
            "source_end": int(override["source_end"]),
        }
        for override in overrides
    ]
    return timing_atoms, public_override_spans


def build_scene_timing_context(
    *,
    project_id: str,
    script_artifact_id: str,
    narration_audio_artifact_id: str,
    narration_word_timing_map_artifact_id: str,
    script: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> dict[str, Any]:
    validate_word_timing_authority(
        project_id=project_id,
        script_artifact_id=script_artifact_id,
        narration_audio_artifact_id=narration_audio_artifact_id,
        narration_word_timing_map_artifact_id=narration_word_timing_map_artifact_id,
        word_timing_map=word_timing_map,
    )
    script_sections = script.get("script_sections")
    timing_sections = _sections(word_timing_map)
    if not isinstance(script_sections, list) or len(script_sections) != len(timing_sections):
        raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "Approved Voice sections do not match current Script sections.", 409, retryable=False)
    timing_by_id = {str(item.get("section_id")): item for item in timing_sections}
    total_sample_count = int(word_timing_map.get("total_sample_count") or 0)
    if total_sample_count <= 0:
        raise AppError("BEAT_TIMING_MAP_INVALID", "NarrationWordTimingMap total sample count is invalid.", 409, retryable=False)
    compact_sections: list[dict[str, Any]] = []
    for order, section in enumerate(script_sections, 1):
        section_id = str(section.get("section_id") or "")
        timing = timing_by_id.get(section_id)
        if timing is None:
            raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "Approved Voice is missing a current Script section.", 409, retryable=False)
        narration = str(section.get("narration") or "")
        authored_start = int(timing.get("authored_section_start") or 0)
        authored_end = int(timing.get("authored_section_end") or 0)
        section_words = [word for word in timing.get("words", []) if isinstance(word, dict)]
        timing_atoms, spoken_override_spans = _scene_timing_atoms(
            narration=narration,
            authored_start=authored_start,
            authored_end=authored_end,
            timing=timing,
            word_timing_map=word_timing_map,
        )
        clauses: list[dict[str, Any]] = []
        previous_end_sample: int | None = None
        for local_start, local_end in _natural_clause_spans(narration):
            source_start = authored_start + local_start
            source_end = min(authored_end, authored_start + local_end)
            matches = [
                word
                for word in section_words
                if int(word.get("authored_end", -1)) > source_start
                and int(word.get("authored_start", -1)) < source_end
            ]
            if not matches:
                continue
            speech_start = int(matches[0]["global_start_sample"])
            speech_end = int(matches[-1]["global_end_sample"])
            pause_before = (
                0
                if previous_end_sample is None
                else max(0, speech_start - previous_end_sample)
            )
            clauses.append({
                "source_start": source_start,
                "source_end": source_end,
                "text": narration[local_start:local_end],
                "start_boundary_safe": not _unsafe_override_boundary(
                    word_timing_map, source_start
                ),
                "end_boundary_safe": not _unsafe_override_boundary(
                    word_timing_map, source_end
                ),
                "speech_start_sample": speech_start,
                "speech_end_sample": speech_end,
                "pause_before_samples": pause_before,
            })
            previous_end_sample = speech_end
        compact_sections.append({
            "section_id": section_id,
            "section_order": order,
            "source_start": authored_start,
            "source_end": authored_end,
            "actual_duration_samples": int(timing.get("sample_count") or 0),
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "timing_atoms": timing_atoms,
            "clauses": clauses,
            "spoken_override_spans": spoken_override_spans,
        })
    return {
        "policy_version": BEAT_TIMING_POLICY_VERSION,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "total_narration_sample_count": total_sample_count,
        "sections": compact_sections,
    }
