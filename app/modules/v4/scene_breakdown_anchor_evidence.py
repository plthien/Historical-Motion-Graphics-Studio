from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class AnchorCategorySpec:
    style_collection: str
    prompt_collection: str
    id_key: str


CATEGORY_SPECS: tuple[AnchorCategorySpec, ...] = (
    AnchorCategorySpec("characters", "characters", "char_id"),
    AnchorCategorySpec("locations", "locations", "loc_id"),
    AnchorCategorySpec("recurring_props", "recurring_props", "prop_id"),
    AnchorCategorySpec(
        "recurring_visual_subjects",
        "recurring_visual_subjects",
        "subject_id",
    ),
    AnchorCategorySpec("motifs", "motifs", "motif_id"),
)

STRUCTURED_ALIAS_FIELDS = (
    "canonical_name",
    "approved_aliases",
    "textual_aliases",
    "aliases",
)


def empty_anchor_spans() -> dict[str, dict[str, list[list[int]]]]:
    return {spec.prompt_collection: {} for spec in CATEGORY_SPECS}


def _word_char(value: str) -> bool:
    return bool(value) and (value.isalnum() or value == "_")


def _has_safe_boundaries(text: str, term: str, start: int, end: int) -> bool:
    if _word_char(term[0]) and start > 0 and _word_char(text[start - 1]):
        return False
    if _word_char(term[-1]) and end < len(text) and _word_char(text[end]):
        return False
    return True


def find_exact_text_spans(text: str, term: str) -> list[list[int]]:
    """Return case-insensitive exact contiguous matches with token boundaries."""

    if not isinstance(text, str) or not isinstance(term, str):
        return []
    needle = term.strip()
    if not needle:
        return []
    spans: list[list[int]] = []
    for match in re.finditer(re.escape(needle), text, flags=re.IGNORECASE):
        start, end = match.span()
        if _has_safe_boundaries(text, needle, start, end):
            spans.append([start, end])
    return spans


def _append_term(result: list[str], seen: set[str], value: Any) -> None:
    if not isinstance(value, str):
        return
    term = value.strip()
    key = term.casefold()
    if term and key not in seen:
        seen.add(key)
        result.append(term)


def authoritative_match_terms(anchor: dict[str, Any]) -> list[str]:
    """Return textual identity terms only.

    Visual traits, continuity notes, descriptions, and style fields are
    deliberately excluded because they are not authoritative textual aliases.
    """

    result: list[str] = []
    seen: set[str] = set()
    _append_term(result, seen, anchor.get("label"))
    for field in STRUCTURED_ALIAS_FIELDS:
        raw = anchor.get(field)
        if isinstance(raw, list):
            for value in raw:
                _append_term(result, seen, value)
        else:
            _append_term(result, seen, raw)
    return result


def _ordered_unique_spans(spans: list[list[int]]) -> list[list[int]]:
    result: list[list[int]] = []
    seen: set[tuple[int, int]] = set()
    for start, end in sorted((int(span[0]), int(span[1])) for span in spans):
        key = (start, end)
        if start < end and key not in seen:
            seen.add(key)
            result.append([start, end])
    return result


def _representative_spans(
    anchor: dict[str, Any],
    *,
    section_id: str,
    section_start: int,
    section_end: int,
) -> list[list[int]]:
    key = "occurrences" if "motif_id" in anchor else "occurrence_quotes"
    spans: list[list[int]] = []
    for occurrence in anchor.get(key, []) if isinstance(anchor.get(key), list) else []:
        if not isinstance(occurrence, dict):
            continue
        quote = occurrence.get("quote")
        position = occurrence.get("resolved_script_position")
        if not isinstance(quote, str) or not isinstance(position, int):
            continue
        occurrence_section = occurrence.get("section_id")
        if isinstance(occurrence_section, str) and occurrence_section != section_id:
            continue
        start = int(position)
        end = start + len(quote)
        if start >= section_start and end <= section_end:
            spans.append([start - section_start, end - section_start])
    return spans


def _presence_spans(
    anchor: dict[str, Any],
    *,
    section_id: str,
    section_start: int,
    section_end: int,
) -> list[list[int]]:
    if "motif_id" in anchor:
        return []
    spans: list[list[int]] = []
    ranges = anchor["presence_ranges"]
    for presence_range in ranges:
        if not isinstance(presence_range, dict) or presence_range.get("section_id") != section_id:
            continue
        span = presence_range.get("resolved_script_span")
        if not isinstance(span, list) or len(span) != 2:
            continue
        start, end = int(span[0]), int(span[1])
        if start >= section_start and end <= section_end and start < end:
            spans.append([start - section_start, end - section_start])
    return spans


def build_anchor_direct_evidence_spans(
    *,
    style_bible: dict[str, Any],
    section_id: str,
    section_excerpt: str,
    section_absolute_start: int,
) -> dict[str, dict[str, list[list[int]]]]:
    """Build section-local direct textual evidence for approved anchors."""

    section_end = section_absolute_start + len(section_excerpt)
    result = empty_anchor_spans()
    for spec in CATEGORY_SPECS:
        entities = style_bible.get(spec.style_collection)
        for anchor in entities if isinstance(entities, list) else []:
            if not isinstance(anchor, dict) or anchor.get("approval_status") != "text_approved":
                continue
            anchor_id = anchor.get(spec.id_key)
            if not isinstance(anchor_id, str):
                continue
            spans = _representative_spans(
                anchor,
                section_id=section_id,
                section_start=section_absolute_start,
                section_end=section_end,
            )
            textual_spans: list[list[int]] = []
            for term in authoritative_match_terms(anchor):
                textual_spans.extend(find_exact_text_spans(section_excerpt, term))
            if spec.style_collection == "motifs":
                spans.extend(textual_spans)
            else:
                # Canonical entity scope belongs to Script Analysis. Exact label/alias
                # matches are useful direct excerpts only when they fall inside an
                # authoritative presence range; a coincidental same-name mention
                # outside that range must never self-authorize the reference.
                presence_spans = _presence_spans(
                    anchor,
                    section_id=section_id,
                    section_start=section_absolute_start,
                    section_end=section_end,
                )
                spans.extend(
                    candidate
                    for candidate in textual_spans
                    if any(
                        int(scope[0]) <= int(candidate[0])
                        and int(candidate[1]) <= int(scope[1])
                        for scope in presence_spans
                    )
                )
            unique_spans = _ordered_unique_spans(spans)
            if unique_spans:
                result[spec.prompt_collection][anchor_id] = unique_spans
    return result


def build_anchor_presence_spans(
    *,
    style_bible: dict[str, Any],
    section_id: str,
    section_excerpt: str,
    section_absolute_start: int,
) -> dict[str, dict[str, list[list[int]]]]:
    """Build section-local authoritative narrative-presence ranges.

    Script Analysis owns these ranges. This function only projects their
    resolved immutable spans into section-local coordinates; it performs no NLP
    or visual inference.
    """

    section_end = section_absolute_start + len(section_excerpt)
    result = empty_anchor_spans()
    for spec in CATEGORY_SPECS:
        if spec.style_collection == "motifs":
            continue
        entities = style_bible.get(spec.style_collection)
        for anchor in entities if isinstance(entities, list) else []:
            if not isinstance(anchor, dict) or anchor.get("approval_status") != "text_approved":
                continue
            anchor_id = anchor.get(spec.id_key)
            if not isinstance(anchor_id, str):
                continue
            spans = _ordered_unique_spans(
                _presence_spans(
                    anchor,
                    section_id=section_id,
                    section_start=section_absolute_start,
                    section_end=section_end,
                )
            )
            if spans:
                result[spec.prompt_collection][anchor_id] = spans
    return result


def build_anchor_assignment_spans(
    *,
    style_bible: dict[str, Any],
    section_id: str,
    section_excerpt: str,
    section_absolute_start: int,
) -> dict[str, dict[str, list[list[int]]]]:
    """Build authoritative assignment support for approved anchors.

    Eligibility is the deterministic union of direct occurrence/name evidence
    and Script-Analysis-owned canonical presence ranges. Immediate beat-to-beat
    visual continuity is handled separately by the Scene Breakdown validator.
    """

    direct = build_anchor_direct_evidence_spans(
        style_bible=style_bible,
        section_id=section_id,
        section_excerpt=section_excerpt,
        section_absolute_start=section_absolute_start,
    )
    presence = build_anchor_presence_spans(
        style_bible=style_bible,
        section_id=section_id,
        section_excerpt=section_excerpt,
        section_absolute_start=section_absolute_start,
    )
    result = empty_anchor_spans()
    for category in result:
        anchor_ids = set(direct.get(category, {})) | set(presence.get(category, {}))
        for anchor_id in anchor_ids:
            spans = list(direct.get(category, {}).get(anchor_id, []))
            spans.extend(presence.get(category, {}).get(anchor_id, []))
            unique_spans = _ordered_unique_spans(spans)
            if unique_spans:
                result[category][anchor_id] = unique_spans
    return result


def refs_for_absolute_span(
    *,
    evidence: dict[str, dict[str, list[list[int]]]],
    absolute_span: list[int] | tuple[int, int],
) -> dict[str, list[str]]:
    start, end = int(absolute_span[0]), int(absolute_span[1])
    refs = empty_anchor_spans()
    for category, by_id in evidence.items():
        for anchor_id, spans in by_id.items():
            if any(start < int(span[1]) and end > int(span[0]) for span in spans):
                refs[category][anchor_id] = []
    return {category: list(by_id) for category, by_id in refs.items()}


__all__ = [
    "CATEGORY_SPECS",
    "STRUCTURED_ALIAS_FIELDS",
    "authoritative_match_terms",
    "build_anchor_assignment_spans",
    "build_anchor_direct_evidence_spans",
    "build_anchor_presence_spans",
    "empty_anchor_spans",
    "find_exact_text_spans",
    "refs_for_absolute_span",
]
