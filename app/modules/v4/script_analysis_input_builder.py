from __future__ import annotations

from typing import Any

from app.api.errors import AppError


def build_script_analysis_input(
    *,
    project_id: str,
    script_revision_id: str,
    script_artifact: dict[str, Any],
) -> dict[str, Any]:
    raw_sections = script_artifact.get("script_sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise AppError(
            "SCRIPT_ANALYSIS_SCRIPT_SECTIONS_INVALID",
            "Script Analysis requires at least one valid Script section.",
            409,
            retryable=False,
        )

    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, section in enumerate(raw_sections, start=1):
        if not isinstance(section, dict):
            raise AppError(
                "SCRIPT_ANALYSIS_SCRIPT_SECTIONS_INVALID",
                "Script Analysis encountered a non-object Script section.",
                409,
                retryable=False,
                details={"section_order": index},
            )
        section_id = str(section.get("section_id", ""))
        if not section_id or section_id in seen:
            raise AppError(
                "SCRIPT_ANALYSIS_SCRIPT_SECTIONS_INVALID",
                "Script section identifiers must be non-empty and unique.",
                409,
                retryable=False,
                details={"section_id": section_id, "section_order": index},
            )
        seen.add(section_id)
        sections.append(
            {
                "section_id": section_id,
                "section_order": index,
                "section_title": str(section.get("section_title", "")),
                "section_role": str(section.get("section_role", "")),
                "narration": str(section.get("narration", "")),
            }
        )

    return {
        "project_id": project_id,
        "source_script_revision_id": script_revision_id,
        "script": {"sections": sections},
    }
