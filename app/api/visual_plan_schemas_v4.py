from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.ids import ARTIFACT_ID_PATTERN

PROJECT_ID_PATTERN = r"^project_[0-9a-f]{12}$"
SECTION_ID_PATTERN = r"^sec_[0-9]{3}$"
SCENE_ID_PATTERN = r"^scene_[0-9]{3,}$"
BEAT_ID_PATTERN = r"^beat_[0-9]{3,}$"
CHAR_ID_PATTERN = r"^char_[0-9]{3}$"
LOC_ID_PATTERN = r"^loc_[0-9]{3}$"
PROP_ID_PATTERN = r"^prop_[0-9]{3}$"
RECURRING_SUBJECT_ID_PATTERN = r"^subject_[0-9]{3}$"
MOTIF_ID_PATTERN = r"^motif_[0-9]{3}$"
CONTINUITY_ID_PATTERN = r"^continuity_[0-9]{3,}$"
VISUAL_CONTEXT_ID_PATTERN = r"^ctx_[0-9]{3}$"
SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"

ArtifactId = Annotated[str, Field(pattern=ARTIFACT_ID_PATTERN)]
ProjectId = Annotated[str, Field(pattern=PROJECT_ID_PATTERN)]
SectionId = Annotated[str, Field(pattern=SECTION_ID_PATTERN)]
SceneId = Annotated[str, Field(pattern=SCENE_ID_PATTERN)]
BeatId = Annotated[str, Field(pattern=BEAT_ID_PATTERN)]
CharacterId = Annotated[str, Field(pattern=CHAR_ID_PATTERN)]
LocationId = Annotated[str, Field(pattern=LOC_ID_PATTERN)]
PropId = Annotated[str, Field(pattern=PROP_ID_PATTERN)]
RecurringSubjectId = Annotated[str, Field(pattern=RECURRING_SUBJECT_ID_PATTERN)]
MotifId = Annotated[str, Field(pattern=MOTIF_ID_PATTERN)]
ContinuityGroupId = Annotated[str, Field(pattern=CONTINUITY_ID_PATTERN)]
VisualContextId = Annotated[str, Field(pattern=VISUAL_CONTEXT_ID_PATTERN)]
NonEmptyText = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
ScriptSpan = tuple[NonNegativeInt, NonNegativeInt]

Framing = Literal[
    "extreme_wide", "wide", "medium_wide", "medium", "medium_close", "close_up",
    "extreme_close_up", "overhead", "insert", "graphic_layout",
]
CameraAngle = Literal["eye_level", "low_angle", "high_angle", "top_down", "dutch_angle", "pov", "neutral_graphic"]
CameraMovement = Literal[
    "static", "slow_push_in", "slow_pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
    "tracking", "subtle_handheld", "parallax", "none",
]
ContinuityRelation = Literal[
    "new_setup", "same_setup", "match_subject", "match_location", "match_composition", "graphic_sequence",
]
RepresentationFamily = Literal[
    "character_environment_lived", "object_evidence_closeup", "text_diagram_editorial",
]
_REPRESENTATION_FAMILIES = {
    "character_environment_lived",
    "object_evidence_closeup",
    "text_diagram_editorial",
}
QCStatus = Literal["passed", "warning", "failed"]
NarrativeMarkerType = Literal["turning_point", "reveal", "contrast", "payoff", "emphasis"]


def _unique(values: list[str], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique values")


def _valid_span(span: ScriptSpan, field_name: str = "resolved_script_span") -> None:
    if span[1] < span[0]:
        raise ValueError(f"{field_name} end must be greater than or equal to start")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualPlanApprovedReferenceBinding(StrictModel):
    anchor_type: Literal["character", "location", "prop", "recurring_visual_subject"]
    anchor_id: Annotated[str, Field(pattern=r"^(char|loc|prop|subject)_[0-9]{3}$")]
    reference_artifact_id: ArtifactId
    media_artifact_id: ArtifactId
    approval_set_artifact_id: ArtifactId
    source_definition_hash: Annotated[str, Field(pattern=SHA256_PATTERN)]
    media_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mime_type: NonEmptyText
    image_artifact_id: ArtifactId

    @model_validator(mode="after")
    def anchor_identity_matches_type(self) -> "VisualPlanApprovedReferenceBinding":
        expected_prefix = {
            "character": "char_",
            "location": "loc_",
            "prop": "prop_",
            "recurring_visual_subject": "subject_",
        }[self.anchor_type]
        if not self.anchor_id.startswith(expected_prefix):
            raise ValueError("approved reference anchor identity must match anchor_type")
        return self


class VisualPlanValidationIssue(StrictModel):
    path: NonEmptyText
    code: NonEmptyText
    message: NonEmptyText


class VisualPlanRepresentationFamilyQC(StrictModel):
    total_beat_count: NonNegativeInt
    counts: dict[str, NonNegativeInt]
    percentages: dict[str, NonNegativeFloat]
    complete_visual_plan: bool

    @model_validator(mode="after")
    def validate_distribution(self) -> "VisualPlanRepresentationFamilyQC":
        if set(self.counts) != _REPRESENTATION_FAMILIES or set(self.percentages) != _REPRESENTATION_FAMILIES:
            raise ValueError("representation-family QC must report every current representation family exactly once")
        if sum(self.counts.values()) != self.total_beat_count:
            raise ValueError("representation-family counts must equal total_beat_count")
        if self.complete_visual_plan and self.total_beat_count < 1:
            raise ValueError("a complete Visual Plan must contain at least one Beat")
        if self.total_beat_count:
            if abs(sum(self.percentages.values()) - 100.0) > 0.02:
                raise ValueError("representation-family percentages must total approximately 100 percent")
        elif any(self.percentages.values()):
            raise ValueError("empty representation-family QC must have zero percentages")
        return self


class VisualPlanValidationReport(StrictModel):
    scene_coverage_status: QCStatus
    beat_coverage_status: QCStatus
    order_status: QCStatus
    source_identity_status: QCStatus
    referential_integrity_status: QCStatus
    treatment_validity_status: QCStatus
    continuity_status: QCStatus
    forbidden_field_status: QCStatus
    reasoning_leakage_status: QCStatus
    missing_scene_ids: list[SceneId]
    duplicate_scene_ids: list[SceneId]
    missing_beat_ids: list[BeatId]
    duplicate_beat_ids: list[BeatId]
    reordered_beat_ids: list[BeatId]
    unknown_ref_errors: list[VisualPlanValidationIssue]
    invalid_treatment_errors: list[VisualPlanValidationIssue]
    continuity_warnings: list[VisualPlanValidationIssue]
    forbidden_field_errors: list[VisualPlanValidationIssue]
    reasoning_leakage_errors: list[VisualPlanValidationIssue]
    representation_family_qc: VisualPlanRepresentationFamilyQC
    warnings: list[NonEmptyText]

    @model_validator(mode="after")
    def unique_ids(self) -> "VisualPlanValidationReport":
        for name in ("missing_scene_ids", "duplicate_scene_ids", "missing_beat_ids", "duplicate_beat_ids", "reordered_beat_ids"):
            _unique(list(getattr(self, name)), name)
        return self


class ReferenceBindingBase(StrictModel):
    reference_artifact_id: ArtifactId
    media_artifact_id: ArtifactId
    approval_set_artifact_id: ArtifactId
    source_definition_hash: Annotated[str, Field(pattern=SHA256_PATTERN)]
    media_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mime_type: NonEmptyText
    image_artifact_id: ArtifactId


class CharacterReferenceBinding(ReferenceBindingBase):
    anchor_type: Literal["character"]
    anchor_id: CharacterId


class LocationReferenceBinding(ReferenceBindingBase):
    anchor_type: Literal["location"]
    anchor_id: LocationId


class PropReferenceBinding(ReferenceBindingBase):
    anchor_type: Literal["prop"]
    anchor_id: PropId


class RecurringVisualSubjectReferenceBinding(ReferenceBindingBase):
    anchor_type: Literal["recurring_visual_subject"]
    anchor_id: RecurringSubjectId


ReferenceBinding = Annotated[
    CharacterReferenceBinding | LocationReferenceBinding | PropReferenceBinding | RecurringVisualSubjectReferenceBinding,
    Field(discriminator="anchor_type"),
]


class VisualPlanBeat(StrictModel):
    beat_id: BeatId
    visual_context_ref: VisualContextId
    representation_family: RepresentationFamily
    selected_character_refs: list[CharacterId]
    selected_location_refs: list[LocationId]
    selected_prop_refs: list[PropId]
    selected_recurring_visual_subject_refs: list[RecurringSubjectId]
    selected_motif_refs: list[MotifId]
    selected_native_visual_reference_ids: list[ArtifactId]
    visual_concept: NonEmptyText
    subject_action: NonEmptyText
    environment_emphasis: NonEmptyText
    framing: Framing
    camera_angle: CameraAngle
    camera_movement: CameraMovement
    composition: NonEmptyText
    lighting_direction: NonEmptyText
    color_emphasis: NonEmptyText
    motion_guidance: NonEmptyText
    continuity_with_previous: ContinuityRelation
    previous_beat_image_reference_required: bool
    continuity_notes: NonEmptyText
    production_notes: NonEmptyText
    negative_requirements: list[NonEmptyText]
    visible_text: list[NonEmptyText]
    scene_id: SceneId
    beat_order: PositiveInt
    local_beat_order: PositiveInt
    script_quote: NonEmptyText
    resolved_script_span: ScriptSpan
    computed_duration_seconds: PositiveFloat
    visual_priority: Literal["critical", "high", "normal"]
    is_narrative_marker: bool
    narrative_marker_refs: list[NonEmptyText]
    narrative_marker_types: list[NarrativeMarkerType]
    continuity_group_id: ContinuityGroupId
    selected_reference_bindings: list[ReferenceBinding]

    @model_validator(mode="after")
    def validate_beat(self) -> "VisualPlanBeat":
        _valid_span(self.resolved_script_span)
        if self.continuity_with_previous == "new_setup" and self.previous_beat_image_reference_required:
            raise ValueError("new_setup must not require the previous Beat image")
        if self.continuity_with_previous == "match_composition" and not self.previous_beat_image_reference_required:
            raise ValueError("match_composition must require the previous Beat image")
        for name in (
            "selected_character_refs", "selected_location_refs", "selected_prop_refs", "selected_recurring_visual_subject_refs",
            "selected_motif_refs", "selected_native_visual_reference_ids", "negative_requirements",
            "visible_text", "narrative_marker_refs", "narrative_marker_types",
        ):
            _unique(list(getattr(self, name)), name)
        binding_keys = [(item.anchor_type, item.anchor_id) for item in self.selected_reference_bindings]
        if len(binding_keys) != len(set(binding_keys)):
            raise ValueError("selected_reference_bindings must be unique by anchor")
        binding_refs = [item.reference_artifact_id for item in self.selected_reference_bindings]
        if set(binding_refs) != set(self.selected_native_visual_reference_ids):
            raise ValueError("selected reference bindings must match selected native visual references")
        for binding in self.selected_reference_bindings:
            if binding.anchor_type == "character" and binding.anchor_id not in self.selected_character_refs:
                raise ValueError("character reference binding must target a selected character ref")
            if binding.anchor_type == "location" and binding.anchor_id not in self.selected_location_refs:
                raise ValueError("location reference binding must target a selected location ref")
            if binding.anchor_type == "prop" and binding.anchor_id not in self.selected_prop_refs:
                raise ValueError("prop reference binding must target a selected prop ref")
            if binding.anchor_type == "recurring_visual_subject" and binding.anchor_id not in self.selected_recurring_visual_subject_refs:
                raise ValueError("recurring subject reference binding must target a selected recurring subject ref")
        return self


class VisualPlanScene(StrictModel):
    scene_id: SceneId
    scene_order: PositiveInt
    section_id: SectionId
    scene_continuity_notes: NonEmptyText
    beat_plans: Annotated[list[VisualPlanBeat], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_scene(self) -> "VisualPlanScene":
        _unique([beat.beat_id for beat in self.beat_plans], "beat_plans.beat_id")
        if any(beat.scene_id != self.scene_id for beat in self.beat_plans):
            raise ValueError("beat scene identity mismatch")
        return self


class VisualPlanModel(StrictModel):
    artifact_type: Literal["visual_plan"]
    schema_version: Literal["3.0"]
    project_id: ProjectId
    visual_plan_revision_id: ArtifactId
    parent_visual_plan_revision_id: ArtifactId | None
    revision_operation: Literal["full_generation", "section_regeneration", "section_edit", "approval"]
    source_script_revision_id: ArtifactId
    source_script_analysis_revision_id: ArtifactId
    source_style_bible_revision_id: ArtifactId
    source_scene_plan_revision_id: ArtifactId
    source_visual_reference_approval_set_artifact_id: ArtifactId | None
    source_run_artifact_id: ArtifactId
    status: Literal["draft_ready", "approved"]
    scene_count: PositiveInt
    beat_count: PositiveInt
    scenes: Annotated[list[VisualPlanScene], Field(min_length=1)]
    backend_qc: VisualPlanValidationReport

    @model_validator(mode="after")
    def validate_plan(self) -> "VisualPlanModel":
        if self.scene_count != len(self.scenes):
            raise ValueError("scene_count must equal scenes length")
        if self.beat_count != sum(len(scene.beat_plans) for scene in self.scenes):
            raise ValueError("beat_count must equal total beat plans")
        if self.revision_operation == "full_generation":
            if self.status != "draft_ready":
                raise ValueError("full generation must be draft_ready")
        elif self.revision_operation in {"section_regeneration", "section_edit"}:
            if self.status != "draft_ready" or self.parent_visual_plan_revision_id is None:
                raise ValueError(f"{self.revision_operation} requires a parent and draft_ready status")
        elif self.status != "approved" or self.parent_visual_plan_revision_id is None:
            raise ValueError("approval requires a parent and approved status")
        _unique([scene.scene_id for scene in self.scenes], "scenes.scene_id")
        _unique([beat.beat_id for scene in self.scenes for beat in scene.beat_plans], "beat_plans.beat_id")
        if [scene.scene_order for scene in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("scene_order must be contiguous and match scene array order")
        all_beats = [beat for scene in self.scenes for beat in scene.beat_plans]
        if [beat.beat_order for beat in all_beats] != list(range(1, len(all_beats) + 1)):
            raise ValueError("beat_order must be contiguous and match global beat order")
        next_group_number = 1
        current_group: str | None = None
        has_previous_beat = False
        for scene in self.scenes:
            if [beat.local_beat_order for beat in scene.beat_plans] != list(range(1, len(scene.beat_plans) + 1)):
                raise ValueError("local_beat_order must be contiguous within every scene")
            for beat in scene.beat_plans:
                if not has_previous_beat and beat.continuity_with_previous != "new_setup":
                    raise ValueError("the first beat of the whole visual plan must use new_setup")
                if current_group is None or beat.continuity_with_previous == "new_setup":
                    expected_group = f"continuity_{next_group_number:03d}"
                    next_group_number += 1
                    if beat.continuity_group_id != expected_group:
                        raise ValueError("continuity group IDs must be globally contiguous in beat order")
                    current_group = beat.continuity_group_id
                elif beat.continuity_group_id != current_group:
                    raise ValueError("non-new_setup beats must remain in the current continuity group across scene boundaries")
                has_previous_beat = True
        critical = (
            "scene_coverage_status", "beat_coverage_status", "order_status", "source_identity_status",
            "referential_integrity_status", "treatment_validity_status", "forbidden_field_status",
            "reasoning_leakage_status",
        )
        if any(getattr(self.backend_qc, name) != "passed" for name in critical):
            raise ValueError("critical Visual Plan QA must pass")
        if self.backend_qc.continuity_status == "failed":
            raise ValueError("continuity QA must not fail")
        return self


class VisualPlanReadResponse(StrictModel):
    project_id: ProjectId
    generated: bool
    artifact_id: ArtifactId | None
    artifact: VisualPlanModel | None

    @model_validator(mode="after")
    def validate_wrapper_identity(self) -> "VisualPlanReadResponse":
        populated = self.artifact_id is not None and self.artifact is not None
        if self.generated != populated:
            raise ValueError("generated flag does not match wrapper content")
        if self.artifact is not None:
            if self.artifact.visual_plan_revision_id != self.artifact_id:
                raise ValueError("wrapper artifact ID must equal visual_plan_revision_id")
            if self.artifact.project_id != self.project_id:
                raise ValueError("wrapper project identity mismatch")
        return self
