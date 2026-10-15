from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.ids import ARTIFACT_ID_PATTERN


class GenerateStyleBibleRequestV4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_analysis_artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN)


class StyleBibleArtifactResponseV4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    artifact: dict[str, Any]

    @model_validator(mode="after")
    def validate_identity(self) -> "StyleBibleArtifactResponseV4":
        if self.artifact.get("style_bible_revision_id") != self.artifact_id:
            raise ValueError("artifact_id must equal artifact.style_bible_revision_id")
        return self


class StyleBibleReadResponseV4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{12}$")
    generated: bool
    artifact_id: str | None = Field(default=None, pattern=ARTIFACT_ID_PATTERN)
    artifact: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "StyleBibleReadResponseV4":
        present = self.artifact_id is not None and self.artifact is not None
        if self.generated != present:
            raise ValueError("generated must match artifact presence")
        if present and self.artifact is not None:
            if self.artifact.get("style_bible_revision_id") != self.artifact_id:
                raise ValueError("artifact identity mismatch")
            if self.artifact.get("project_id") != self.project_id:
                raise ValueError("project identity mismatch")
        return self


class StyleBibleTextUpdateRequestV4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_style_bible_revision_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    editable_content: dict[str, Any]


class StyleBibleTextApproveRequestV4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_style_bible_revision_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
