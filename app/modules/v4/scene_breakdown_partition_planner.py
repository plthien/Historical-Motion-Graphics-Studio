from __future__ import annotations

import json
from typing import Any

from app.api.errors import AppError
from app.modules.v4.beat_timing_map import project_visual_intervals_for_source_spans


_BOUNDARY_CLOSERS = frozenset("\"'”’»％)]")


def _boundary_kind(source_offset: int, clauses: list[dict[str, Any]]) -> str:
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        if int(clause.get("source_end", -1)) != source_offset:
            continue
        text = str(clause.get("text") or "").rstrip()
        while text and text[-1] in _BOUNDARY_CLOSERS:
            text = text[:-1].rstrip()
        if text.endswith(";"):
            return "semicolon"
        if text.endswith((".", "!", "?")):
            return "sentence"
        return "clause"
    return "word"


def _canonical_scene_grid(
    *,
    scene: dict[str, Any],
    timing_section: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[int]]:
    """Build the complete technically legal timing-atom boundary grid once."""

    atoms = timing_section.get("timing_atoms")
    if not isinstance(atoms, list) or not atoms:
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_TIMING_INVALID",
            "Approved Voice timing atoms are unavailable for Beat boundary planning.",
            409,
            retryable=False,
        )
    sample_rate = int(word_timing_map.get("sample_rate_hz") or 0)
    if sample_rate <= 0:
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_TIMING_INVALID",
            "Approved Voice sample rate is invalid.",
            409,
            retryable=False,
        )

    source_start = int(scene["source_start"])
    source_end = int(scene["source_end"])
    script_text = str(scene["script_text"])
    clauses = timing_section.get("clauses")
    clauses = clauses if isinstance(clauses, list) else []
    scene_atoms = [
        atom
        for atom in atoms
        if int(atom["source_end"]) > source_start
        and int(atom["source_start"]) < source_end
    ]
    if not scene_atoms:
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_TIMING_INVALID",
            "Micro-Scene cannot be mapped to approved Voice timing atoms.",
            422,
            retryable=False,
        )

    boundaries: list[dict[str, Any]] = []
    for atom in atoms:
        atom_end = int(atom["source_end"])
        if not (source_start < atom_end < source_end):
            continue
        if atom.get("end_boundary_safe") is not True:
            continue
        boundaries.append(
            {
                "boundary_id": f"boundary_{len(boundaries) + 1:03d}",
                "source_offset": atom_end,
                "natural_boundary": _boundary_kind(atom_end, clauses),
            }
        )

    offsets = [
        source_start,
        *(int(item["source_offset"]) for item in boundaries),
        source_end,
    ]
    projected = project_visual_intervals_for_source_spans(
        word_timing_map,
        [[left, right] for left, right in zip(offsets[:-1], offsets[1:], strict=True)],
    )
    if len(projected) != len(offsets) - 1:
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_TIMING_INVALID",
            "Canonical timing projection did not cover the legal Beat boundary grid.",
            500,
            retryable=False,
        )

    point_samples = [int(projected[0]["visual_start_sample"])]
    for index in range(1, len(projected)):
        previous_end = int(projected[index - 1]["visual_end_sample"])
        current_start = int(projected[index]["visual_start_sample"])
        if previous_end != current_start:
            raise AppError(
                "SCENE_BREAKDOWN_PARTITION_TIMING_INVALID",
                "Canonical adjacent visual intervals are not contiguous.",
                500,
                retryable=False,
                details={
                    "scene_key": scene.get("scene_key"),
                    "boundary_index": index,
                    "previous_visual_end_sample": previous_end,
                    "current_visual_start_sample": current_start,
                },
            )
        point_samples.append(current_start)
    point_samples.append(int(projected[-1]["visual_end_sample"]))

    scene_visual_start = point_samples[0]
    for index, boundary in enumerate(boundaries):
        boundary["time_from_scene_start_seconds"] = round(
            (point_samples[index + 1] - scene_visual_start) / sample_rate,
            3,
        )
    if len(script_text) != source_end - source_start:
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_SOURCE_INVALID",
            "Scene source span does not match the frozen Script text.",
            500,
            retryable=False,
            details={"scene_key": scene.get("scene_key")},
        )
    return boundaries, point_samples


def build_legal_boundary_plan(
    *,
    scene: dict[str, Any],
    timing_section: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> dict[str, Any]:
    """Return fixed Scene source plus every technically legal Beat boundary."""

    boundaries, point_samples = _canonical_scene_grid(
        scene=scene,
        timing_section=timing_section,
        word_timing_map=word_timing_map,
    )
    sample_rate = int(word_timing_map.get("sample_rate_hz") or 0)
    return {
        "scene_key": str(scene["scene_key"]),
        "visual_context_ref": str(scene["visual_context_ref"]),
        "source_start": int(scene["source_start"]),
        "source_end": int(scene["source_end"]),
        "script_text": str(scene["script_text"]),
        "duration_seconds": round(
            (point_samples[-1] - point_samples[0]) / sample_rate,
            3,
        ),
        "legal_boundaries": boundaries,
    }


def build_legal_boundaries(
    *,
    scene: dict[str, Any],
    timing_section: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return all technically legal canonical timing-atom Beat boundaries."""

    return build_legal_boundary_plan(
        scene=scene,
        timing_section=timing_section,
        word_timing_map=word_timing_map,
    )["legal_boundaries"]


def _authoring_segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Represent fixed Scene source once with the complete legal ID lattice."""

    source_start = int(plan["source_start"])
    source_end = int(plan["source_end"])
    script_text = str(plan["script_text"])
    segments: list[dict[str, Any]] = []
    cursor = source_start
    for boundary in plan["legal_boundaries"]:
        endpoint = int(boundary["source_offset"])
        if not cursor < endpoint < source_end:
            raise AppError(
                "SCENE_BREAKDOWN_PARTITION_BOUNDARY_INVALID",
                "Legal Beat boundary is outside the Scene source span.",
                500,
                retryable=False,
            )
        segments.append(
            {
                "text": script_text[cursor - source_start : endpoint - source_start],
                "boundary_id_after": str(boundary["boundary_id"]),
            }
        )
        cursor = endpoint
    segments.append(
        {
            "text": script_text[cursor - source_start : source_end - source_start],
            "boundary_id_after": None,
        }
    )
    if "".join(str(item["text"]) for item in segments) != script_text:
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_BOUNDARY_INVALID",
            "Scene source segments do not reconstruct the frozen Script exactly.",
            500,
            retryable=False,
        )
    return segments


def legal_boundary_ids_for_authoring(plan: dict[str, Any]) -> set[str]:
    return {
        str(item["boundary_id"])
        for item in plan.get("legal_boundaries", [])
        if isinstance(item, dict)
    }


def project_scene_authoring_input(plan: dict[str, Any]) -> dict[str, Any]:
    """Project only fixed source and the complete legal Beat decision surface."""

    return {
        "scene_key": str(plan["scene_key"]),
        "visual_context_ref": str(plan["visual_context_ref"]),
        "source_segments": _authoring_segments(plan),
    }


def measure_scene_authoring_input(payload: dict[str, Any]) -> dict[str, int]:
    """Deterministic internal metrics for Step-1 request-size regression tests."""

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    source_chars = 0
    legal_boundary_count = 0
    scene_count = 0
    sections = payload.get("scene_authoring_plan", {}).get("sections", [])
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        scenes = section.get("scenes", [])
        for scene in scenes if isinstance(scenes, list) else []:
            if not isinstance(scene, dict):
                continue
            scene_count += 1
            segments = scene.get("source_segments", [])
            source_chars += sum(
                len(str(item.get("text") or ""))
                for item in segments
                if isinstance(item, dict)
            )
            legal_boundary_count += sum(
                1
                for item in segments
                if isinstance(item, dict) and item.get("boundary_id_after") is not None
            )
    return {
        "source_narration_characters": source_chars,
        "serialized_input_characters": len(serialized),
        "serialized_input_bytes": len(serialized.encode("utf-8")),
        "legal_boundary_count": legal_boundary_count,
        "scene_count": scene_count,
    }


__all__ = [
    "build_legal_boundaries",
    "build_legal_boundary_plan",
    "legal_boundary_ids_for_authoring",
    "measure_scene_authoring_input",
    "project_scene_authoring_input",
]
