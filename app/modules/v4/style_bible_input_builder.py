from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.api.errors import AppError
from app.modules.v4.contracts import V4ContractValidator


class StyleBibleInputBuilderV4:
    """Project Script Analysis continuity data into the Style Bible input boundary."""

    def __init__(self) -> None:
        self.validator = V4ContractValidator()

    def build(
        self,
        *,
        project_id: str,
        script_analysis_artifact_id: str,
        script_analysis_payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.validator.validate(
            "script_analysis_v1.schema.json",
            script_analysis_payload,
            error_code="STYLE_BIBLE_SCRIPT_ANALYSIS_INVALID",
        )
        if script_analysis_payload.get("project_id") != project_id:
            raise AppError(
                "STYLE_BIBLE_SCRIPT_ANALYSIS_PROJECT_MISMATCH",
                "The current Script Analysis belongs to another project.",
                409,
                retryable=False,
            )
        payload = {
            "schema_version": "1.0",
            "project_id": project_id,
            "script_analysis_artifact_id": script_analysis_artifact_id,
            "source_script_revision_id": script_analysis_payload["source_script_revision_id"],
            "continuity_catalog": deepcopy(script_analysis_payload["continuity_catalog"]),
        }
        return self.validator.validate(
            "style_bible_input_v1.schema.json",
            payload,
            error_code="STYLE_BIBLE_INPUT_INVALID",
        )
