from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from app.api.errors import AppError
from app.modules.v4.script_word_budget import SCRIPT_SECTION_SEPARATOR, build_full_script_from_sections
from app.modules.v4.voice_first_policy import (
    DELIVERY_POLICY_VERSION,
    PAUSE_SECONDS_BY_STRENGTH,
    SPOKEN_TEXT_POLICY_VERSION,
)


@dataclass(frozen=True, slots=True)
class CompiledVoiceScript:
    spoken_script: dict[str, Any]
    sections: tuple[dict[str, Any], ...]
    compilation_sha256: str


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def pause_seconds_for_strength(strength: str) -> float:
    seconds = PAUSE_SECONDS_BY_STRENGTH.get(strength)
    if seconds is None:
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Delivery cue pause strength is invalid.", 409, retryable=False)
    return float(seconds)


def _break_tag(strength: str) -> str:
    seconds = pause_seconds_for_strength(strength)
    return f'<break time="{seconds:.2f}s" />'


def compile_voice_spoken_text(script: dict[str, Any]) -> CompiledVoiceScript:
    sections = script.get("script_sections")
    if not isinstance(sections, list) or not sections:
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Canonical Script sections are unavailable.", 409, retryable=False)

    spoken_sections: list[dict[str, Any]] = []
    compiled: list[dict[str, Any]] = []
    authored_section_start = 0
    for order, raw_section in enumerate(sections, 1):
        if not isinstance(raw_section, dict):
            raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Canonical Script section is malformed.", 409, retryable=False)
        section_id = raw_section.get("section_id")
        narration = raw_section.get("narration")
        overrides = raw_section.get("spoken_overrides")
        cues = raw_section.get("delivery_cues")
        if (
            not isinstance(section_id, str)
            or not section_id
            or not isinstance(narration, str)
            or not isinstance(overrides, list)
            or not isinstance(cues, list)
        ):
            raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Canonical spoken-delivery data is missing.", 409, retryable=False)

        ordered_overrides = sorted(
            (item for item in overrides if isinstance(item, dict)),
            key=lambda item: (int(item.get("source_start", -1)), str(item.get("id") or item.get("spoken_override_id", ""))),
        )
        cursor = 0
        spoken_cursor = 0
        parts: list[str] = []
        mapping: list[dict[str, Any]] = []
        authored_to_spoken_boundaries: dict[int, int] = {0: 0}
        unsafe_authored_boundaries: set[int] = set()
        unsafe_spoken_boundaries: set[int] = set()
        for override in ordered_overrides:
            global_start = override.get("source_start")
            global_end = override.get("source_end")
            spoken_text = override.get("spoken_text")
            if not isinstance(global_start, int) or not isinstance(global_end, int):
                raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Spoken override source span is invalid.", 409, retryable=False)
            start = global_start - authored_section_start
            end = global_end - authored_section_start
            if not 0 <= start < end <= len(narration):
                raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Spoken override source span is outside its Script section.", 409, retryable=False)
            if start < cursor or not isinstance(spoken_text, str) or not spoken_text.strip() or "<" in spoken_text or ">" in spoken_text:
                raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Spoken override content is invalid.", 409, retryable=False)
            unchanged = narration[cursor:start]
            parts.append(unchanged)
            for index in range(cursor, start + 1):
                authored_to_spoken_boundaries[index] = spoken_cursor + (index - cursor)
            spoken_cursor += len(unchanged)
            spoken_start = spoken_cursor
            parts.append(spoken_text)
            spoken_cursor += len(spoken_text)
            spoken_end = spoken_cursor
            mapping.append(
                {
                    "spoken_override_id": override.get("id") or override.get("spoken_override_id"),
                    "authored_start": global_start,
                    "authored_end": global_end,
                    "section_authored_start": start,
                    "section_authored_end": end,
                    "spoken_start": spoken_start,
                    "spoken_end": spoken_end,
                    "authored_text_sha256": hashlib.sha256(narration[start:end].encode("utf-8")).hexdigest(),
                    "spoken_text_sha256": hashlib.sha256(spoken_text.encode("utf-8")).hexdigest(),
                }
            )
            authored_to_spoken_boundaries[start] = spoken_start
            authored_to_spoken_boundaries[end] = spoken_end
            unsafe_authored_boundaries.update(range(start + 1, end))
            unsafe_spoken_boundaries.update(range(spoken_start + 1, spoken_end))
            cursor = end
        tail = narration[cursor:]
        parts.append(tail)
        for index in range(cursor, len(narration) + 1):
            authored_to_spoken_boundaries[index] = spoken_cursor + (index - cursor)
        canonical_spoken = "".join(parts)
        if "<break" in canonical_spoken.lower() or "<" in canonical_spoken or ">" in canonical_spoken:
            raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Canonical spoken text must not contain provider markup.", 409, retryable=False)

        insertions: list[tuple[int, str, str, float]] = []
        for cue in cues:
            if not isinstance(cue, dict) or cue.get("kind") != "pause_after":
                raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Delivery cue is invalid.", 409, retryable=False)
            global_boundary = cue.get("source_offset")
            if not isinstance(global_boundary, int):
                raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Delivery cue boundary cannot be mapped to spoken text.", 409, retryable=False)
            authored_boundary = global_boundary - authored_section_start
            if authored_boundary in unsafe_authored_boundaries:
                raise AppError("VOICE_AUTHORED_BOUNDARY_UNSAFE", "Delivery cue boundary falls inside a spoken override span.", 409, retryable=False)
            if authored_boundary not in authored_to_spoken_boundaries:
                raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Delivery cue boundary cannot be mapped to spoken text.", 409, retryable=False)
            insertions.append(
                (
                    authored_to_spoken_boundaries[authored_boundary],
                    _break_tag(str(cue.get("pause_strength"))),
                    str(cue.get("id") or cue.get("delivery_cue_id", "")),
                    pause_seconds_for_strength(str(cue.get("pause_strength"))),
                )
            )
        insertions.sort(key=lambda item: (item[0], item[2]))
        provider_parts: list[str] = []
        provider_cursor = 0
        provider_insertions: list[dict[str, Any]] = []
        for spoken_offset, markup, cue_id, pause_seconds in insertions:
            provider_parts.append(canonical_spoken[provider_cursor:spoken_offset])
            provider_parts.append(markup)
            provider_insertions.append({"spoken_offset": spoken_offset, "markup": markup, "pause_seconds": pause_seconds, "delivery_cue_id": cue_id})
            provider_cursor = spoken_offset
        provider_parts.append(canonical_spoken[provider_cursor:])
        provider_text = "".join(provider_parts)
        safe_boundaries = [index for index in range(len(narration) + 1) if index not in unsafe_authored_boundaries]
        compiled_section = {
            "section_id": section_id,
            "section_order": order,
            "authored_text_sha256": hashlib.sha256(narration.encode("utf-8")).hexdigest(),
            "canonical_spoken_text": canonical_spoken,
            "canonical_spoken_text_sha256": hashlib.sha256(canonical_spoken.encode("utf-8")).hexdigest(),
            "provider_text": provider_text,
            "provider_text_sha256": hashlib.sha256(provider_text.encode("utf-8")).hexdigest(),
            "authored_spoken_mapping": mapping,
            "authored_spoken_mapping_sha256": _hash(mapping),
            "safe_authored_boundaries": safe_boundaries,
            "unsafe_spoken_boundaries": sorted(unsafe_spoken_boundaries),
            "provider_insertions": provider_insertions,
            "spoken_text_policy_version": SPOKEN_TEXT_POLICY_VERSION,
            "delivery_policy_version": DELIVERY_POLICY_VERSION,
        }
        compiled.append(compiled_section)
        spoken_section = deepcopy(raw_section)
        spoken_section["narration"] = canonical_spoken
        spoken_sections.append(spoken_section)
        authored_section_start += len(narration)
        if order < len(sections):
            authored_section_start += len(SCRIPT_SECTION_SEPARATOR)

    spoken_script = deepcopy(script)
    spoken_script["script_sections"] = spoken_sections
    spoken_script["full_script"] = build_full_script_from_sections(spoken_sections)
    compilation_payload = {
        "spoken_text_policy_version": SPOKEN_TEXT_POLICY_VERSION,
        "delivery_policy_version": DELIVERY_POLICY_VERSION,
        "sections": compiled,
    }
    return CompiledVoiceScript(
        spoken_script=spoken_script,
        sections=tuple(compiled),
        compilation_sha256=_hash(compilation_payload),
    )


def provider_text_for_section(compilation: CompiledVoiceScript, section_id: str) -> str:
    matches = [item for item in compilation.sections if item.get("section_id") == section_id]
    if len(matches) != 1:
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled Voice section is missing.", 409, retryable=False)
    return str(matches[0]["provider_text"])


def forbidden_spoken_boundaries_by_section(compilation: CompiledVoiceScript) -> dict[str, set[int]]:
    return {
        str(item["section_id"]): {int(value) for value in item.get("unsafe_spoken_boundaries", [])}
        for item in compilation.sections
    }


def provider_text_for_segment(compilation: CompiledVoiceScript, segment: dict[str, Any]) -> str:
    """Render provider-only markup for one canonical spoken segment.

    Segmentation offsets stay anchored to the markup-free spoken Script. Break
    tags are inserted only after the final split is known, so neither source
    offsets nor WPM accounting can be contaminated by provider markup.
    """
    section_id = str(segment.get("section_id") or "")
    section_matches = [item for item in compilation.sections if item.get("section_id") == section_id]
    if len(section_matches) != 1:
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled Voice section is missing for segment.", 409, retryable=False)
    section = section_matches[0]
    order = int(section["section_order"])
    spoken_sections = compilation.spoken_script.get("script_sections")
    if not isinstance(spoken_sections, list) or not 1 <= order <= len(spoken_sections):
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled Voice section order is invalid.", 409, retryable=False)

    section_start = 0
    for index, raw in enumerate(spoken_sections[: order - 1]):
        if not isinstance(raw, dict) or not isinstance(raw.get("narration"), str):
            raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled Voice Script is malformed.", 409, retryable=False)
        section_start += len(str(raw["narration"]).strip()) + len(SCRIPT_SECTION_SEPARATOR)
    canonical = str(section["canonical_spoken_text"])
    source_start = int(segment.get("source_start", -1))
    source_end = int(segment.get("source_end", -1))
    relative_start = source_start - section_start
    relative_end = source_end - section_start
    if relative_start < 0 or relative_start > len(canonical) or relative_end <= relative_start:
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Voice segment is outside its compiled section.", 409, retryable=False)
    canonical_end = min(relative_end, len(canonical))
    text = canonical[relative_start:canonical_end]
    pieces: list[str] = []
    cursor = relative_start
    for insertion in section.get("provider_insertions", []):
        if not isinstance(insertion, dict):
            raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled provider insertion is malformed.", 409, retryable=False)
        offset = int(insertion.get("spoken_offset", -1))
        # pause_after belongs to the preceding chunk when a split lands exactly
        # on its boundary, preventing duplicate breaks across requests.
        if relative_start < offset <= canonical_end or (relative_start == 0 and offset == 0):
            pieces.append(canonical[cursor:offset])
            pieces.append(str(insertion.get("markup") or ""))
            cursor = offset
    pieces.append(canonical[cursor:canonical_end])
    rendered = "".join(pieces)
    if relative_end > len(canonical):
        separator_count = min(relative_end - len(canonical), len(SCRIPT_SECTION_SEPARATOR))
        rendered += SCRIPT_SECTION_SEPARATOR[:separator_count]
    if rendered.replace("\r\n", "\n").replace("\r", "\n") == "":
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Voice provider segment text is empty.", 409, retryable=False)
    return rendered


def kokoro_pieces_for_segment(compilation: CompiledVoiceScript, segment: dict[str, Any]) -> list[dict[str, Any]]:
    """Render a provider-markup-free Kokoro synthesis plan for one segment.

    Text pieces are synthesized by Kokoro. Pause pieces become deterministic
    silence in the adapter. The returned transcript text is always exactly the
    same spoken text represented by the segment and contains no provider markup.
    """
    section_id = str(segment.get("section_id") or "")
    section_matches = [item for item in compilation.sections if item.get("section_id") == section_id]
    if len(section_matches) != 1:
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled Voice section is missing for segment.", 409, retryable=False)
    section = section_matches[0]
    order = int(section["section_order"])
    spoken_sections = compilation.spoken_script.get("script_sections")
    if not isinstance(spoken_sections, list) or not 1 <= order <= len(spoken_sections):
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled Voice section order is invalid.", 409, retryable=False)

    section_start = 0
    for raw in spoken_sections[: order - 1]:
        if not isinstance(raw, dict) or not isinstance(raw.get("narration"), str):
            raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled Voice Script is malformed.", 409, retryable=False)
        section_start += len(str(raw["narration"]).strip()) + len(SCRIPT_SECTION_SEPARATOR)
    canonical = str(section["canonical_spoken_text"])
    source_start = int(segment.get("source_start", -1))
    source_end = int(segment.get("source_end", -1))
    relative_start = source_start - section_start
    relative_end = source_end - section_start
    if relative_start < 0 or relative_start > len(canonical) or relative_end <= relative_start:
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Voice segment is outside its compiled section.", 409, retryable=False)
    canonical_end = min(relative_end, len(canonical))

    pieces: list[dict[str, Any]] = []
    cursor = relative_start
    for insertion in section.get("provider_insertions", []):
        if not isinstance(insertion, dict):
            raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Compiled provider insertion is malformed.", 409, retryable=False)
        offset = int(insertion.get("spoken_offset", -1))
        if relative_start < offset <= canonical_end or (relative_start == 0 and offset == 0):
            text = canonical[cursor:offset]
            if text:
                pieces.append({"kind": "text", "text": text})
            pieces.append({"kind": "silence", "duration_seconds": float(insertion.get("pause_seconds") or 0.0)})
            cursor = offset
    tail = canonical[cursor:canonical_end]
    if tail:
        pieces.append({"kind": "text", "text": tail})
    if relative_end > len(canonical):
        separator_count = min(relative_end - len(canonical), len(SCRIPT_SECTION_SEPARATOR))
        separator = SCRIPT_SECTION_SEPARATOR[:separator_count]
        if separator:
            pieces.append({"kind": "text", "text": separator})
    if not pieces or not any(item.get("kind") == "text" and str(item.get("text") or "").strip() for item in pieces):
        raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Voice provider segment text is empty.", 409, retryable=False)
    return pieces
