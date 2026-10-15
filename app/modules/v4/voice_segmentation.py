from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import re
from typing import Any

from app.api.errors import AppError
from app.modules.v4.script_word_budget import SCRIPT_SECTION_SEPARATOR, build_full_script_from_sections

SEGMENTATION_VERSION = "narration_segmentation_v2"
_PARAGRAPH_END = re.compile(r"\n[ \t]*\n+", flags=re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?](?:[\"'”’»)]*)\s+", flags=re.UNICODE)
_PUNCT_END = re.compile(r"[,;:—–-](?:[\"'”’»)]*)\s+", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+", flags=re.UNICODE)
_NONSPACE = re.compile(r"\S+", flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class NarrationSegment:
    segment_id: str
    segment_order: int
    section_id: str
    source_start: int
    source_end: int
    exact_text: str
    text_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pick_boundary(chunk: str, limit: int, *, forbidden_boundaries: set[int] | None = None) -> int:
    if len(chunk) <= limit:
        if forbidden_boundaries and len(chunk) in forbidden_boundaries:
            raise AppError(
                "VOICE_SEGMENT_SAFE_BOUNDARY_UNAVAILABLE",
                "Voice provider limit lands inside a protected spoken override.",
                422,
                retryable=False,
            )
        return len(chunk)
    forbidden = forbidden_boundaries or set()
    window = chunk[: limit + 1]
    for pattern in (_PARAGRAPH_END, _SENTENCE_END, _PUNCT_END, _WHITESPACE):
        matches = list(pattern.finditer(window))
        for match in reversed(matches):
            end = match.end()
            if 0 < end <= limit and end not in forbidden:
                return end
    token = _NONSPACE.match(chunk)
    if token and token.end() > limit:
        raise AppError(
            "VOICE_SEGMENT_TOKEN_TOO_LARGE",
            "A narration token exceeds the configured provider segment limit.",
            422,
            retryable=False,
            details={"token_length": token.end(), "maximum_characters": limit},
        )
    raise AppError(
        "VOICE_SEGMENT_SAFE_BOUNDARY_UNAVAILABLE",
        "No safe spoken-text boundary exists within the provider segment limit.",
        422,
        retryable=False,
        details={"maximum_characters": limit},
    )


def _section_ranges(script: dict[str, Any]) -> list[tuple[str, int, int]]:
    sections = script.get("script_sections")
    if not isinstance(sections, list) or not sections:
        raise AppError("VOICE_SCRIPT_INVALID", "The current Script has no canonical sections.", 409, retryable=False)
    full_script = str(script.get("full_script", ""))
    canonical = build_full_script_from_sections(sections)
    if canonical != full_script:
        raise AppError("VOICE_SCRIPT_INVALID", "The current Script canonical text is inconsistent.", 409, retryable=False)
    ranges: list[tuple[str, int, int]] = []
    cursor = 0
    for index, section in enumerate(sections):
        section_id = section.get("section_id")
        narration = section.get("narration")
        if not isinstance(section_id, str) or not section_id or not isinstance(narration, str):
            raise AppError("VOICE_SCRIPT_INVALID", "The current Script section identity is invalid.", 409, retryable=False)
        canonical_text = narration.replace("\r\n", "\n").replace("\r", "\n").strip()
        end = cursor + len(canonical_text)
        if index < len(sections) - 1:
            end += len(SCRIPT_SECTION_SEPARATOR)
        ranges.append((section_id, cursor, end))
        cursor = end
    if cursor != len(full_script):
        raise AppError("VOICE_SCRIPT_INVALID", "The Script section ranges do not cover the canonical narration.", 409, retryable=False)
    return ranges


def segment_narration(
    script: dict[str, Any],
    *,
    maximum_characters: int,
    forbidden_boundaries_by_section: dict[str, set[int]] | None = None,
) -> dict[str, Any]:
    if maximum_characters < 16:
        raise AppError("VOICE_SEGMENT_LIMIT_INVALID", "The TTS segment character limit is too small.", 422, retryable=False)
    full_script = str(script.get("full_script", ""))
    if not full_script:
        raise AppError("VOICE_SCRIPT_INVALID", "The current Script narration is empty.", 409, retryable=False)
    segments: list[NarrationSegment] = []
    for section_id, section_start, section_end in _section_ranges(script):
        cursor = section_start
        section_forbidden = (forbidden_boundaries_by_section or {}).get(section_id, set())
        while cursor < section_end:
            relative_cursor = cursor - section_start
            forbidden_relative = {
                int(boundary) - relative_cursor
                for boundary in section_forbidden
                if relative_cursor < int(boundary) <= relative_cursor + maximum_characters
            }
            relative = _pick_boundary(
                full_script[cursor:section_end],
                maximum_characters,
                forbidden_boundaries=forbidden_relative,
            )
            if relative <= 0:
                raise AppError("VOICE_SEGMENTATION_FAILED", "Narration segmentation made no progress.", 500, retryable=False)
            end = cursor + relative
            exact = full_script[cursor:end]
            identity = hashlib.sha256(
                f"{SEGMENTATION_VERSION}\0{section_id}\0{cursor}\0{end}\0".encode("utf-8") + exact.encode("utf-8")
            ).hexdigest()[:16]
            segments.append(
                NarrationSegment(
                    segment_id=f"voice_segment_{identity}",
                    segment_order=len(segments) + 1,
                    section_id=section_id,
                    source_start=cursor,
                    source_end=end,
                    exact_text=exact,
                    text_sha256=_sha256_text(exact),
                )
            )
            cursor = end
    validate_segmentation(full_script, segments)
    payload = {
        "segmentation_version": SEGMENTATION_VERSION,
        "maximum_characters": maximum_characters,
        "segments": [item.to_dict() for item in segments],
    }
    payload["segmentation_input_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def validate_segmentation(full_script: str, segments: list[NarrationSegment] | list[dict[str, Any]]) -> None:
    if not segments:
        raise AppError("VOICE_SEGMENTATION_FAILED", "Narration segmentation produced no segments.", 422, retryable=False)
    cursor = 0
    rebuilt: list[str] = []
    seen_ids: set[str] = set()
    for expected_order, raw in enumerate(segments, 1):
        item = raw if isinstance(raw, NarrationSegment) else NarrationSegment(**raw)
        if item.segment_id in seen_ids or item.segment_order != expected_order:
            raise AppError("VOICE_SEGMENTATION_FAILED", "Narration segment identity or order is invalid.", 422, retryable=False)
        seen_ids.add(item.segment_id)
        if item.source_start != cursor or item.source_end <= item.source_start:
            raise AppError("VOICE_SEGMENTATION_FAILED", "Narration segments have a gap or overlap.", 422, retryable=False)
        exact = full_script[item.source_start:item.source_end]
        if exact != item.exact_text or _sha256_text(exact) != item.text_sha256:
            raise AppError("VOICE_SEGMENTATION_FAILED", "Narration segment text or hash is invalid.", 422, retryable=False)
        rebuilt.append(exact)
        cursor = item.source_end
    if cursor != len(full_script) or "".join(rebuilt) != full_script:
        raise AppError("VOICE_SEGMENTATION_FAILED", "Narration segmentation does not exactly reconstruct the Script.", 422, retryable=False)
