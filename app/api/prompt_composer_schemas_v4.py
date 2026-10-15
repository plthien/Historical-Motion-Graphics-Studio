from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas import VisualStyleSnapshotModel

from app.api.visual_plan_schemas_v4 import (
    ArtifactId,
    BeatId,
    CharacterId,
    ContinuityGroupId,
    ContinuityRelation,
    LocationId,
    MotifId,
    ProjectId,
    PropId,
    RepresentationFamily,
    RecurringSubjectId,
    SceneId,
    VisualContextId,
)

NonEmptyText = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PromptItemId = Annotated[str, Field(pattern=r"^prompt_[0-9]{3,}$")]
PromptHash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
QCStatus = Literal["passed", "warning", "failed"]
AspectRatio = Literal["1:1", "3:4", "4:3", "9:16", "16:9"]
VisualPriority = Literal["critical", "high", "normal"]
NON_NEW_SETUP_CONTINUITY_RELATIONS = tuple(value for value in get_args(ContinuityRelation) if value != "new_setup")
ContinuityDependencyRelation = Literal[*NON_NEW_SETUP_CONTINUITY_RELATIONS]
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptComposerOptions(StrictModel):
    schema_version: Literal["1.0"]
    prompt_language: Literal["en"]
    aspect_ratio: AspectRatio
    target_asset_role: Literal["beat_visual"]
    image_count: Literal[1]


class PromptComposerCharacter(StrictModel):
    char_id: CharacterId
    label: NonEmptyText
    description: NonEmptyText
    explicit_visual_traits: list[NonEmptyText]
    identity_markers: list[NonEmptyText]
    continuity_notes: list[NonEmptyText]
    physical_constraints: list[NonEmptyText]


class PromptComposerLocation(StrictModel):
    loc_id: LocationId
    label: NonEmptyText
    description: NonEmptyText
    explicit_visual_traits: list[NonEmptyText]
    identity_markers: list[NonEmptyText]
    continuity_notes: list[NonEmptyText]
    physical_constraints: list[NonEmptyText]


class PromptComposerProp(StrictModel):
    prop_id: PropId
    label: NonEmptyText
    description: NonEmptyText
    explicit_visual_traits: list[NonEmptyText]
    identity_markers: list[NonEmptyText]
    continuity_notes: list[NonEmptyText]
    physical_constraints: list[NonEmptyText]


class PromptComposerRecurringVisualSubject(StrictModel):
    subject_id: RecurringSubjectId
    label: NonEmptyText
    description: NonEmptyText
    visual_type: NonEmptyText
    explicit_visual_traits: list[NonEmptyText]
    continuity_notes: list[NonEmptyText]
    physical_constraints: list[NonEmptyText]


class PromptComposerVisualContext(StrictModel):
    mode: Literal[
        "story_world",
        "representative_scenario",
        "evidence_view",
        "reconstruction",
        "neutral_abstract",
        "mixed_context",
    ]
    timeframe: NonEmptyText | None
    setting: NonEmptyText | None
    constraints: list[NonEmptyText]


class PromptComposerMotif(StrictModel):
    motif_id: MotifId
    description: NonEmptyText


class PromptComposerStyleEntities(StrictModel):
    characters: list[PromptComposerCharacter]
    locations: list[PromptComposerLocation]
    recurring_props: list[PromptComposerProp]
    recurring_visual_subjects: list[PromptComposerRecurringVisualSubject]
    motifs: list[PromptComposerMotif]

    @model_validator(mode="after")
    def unique_ids(self) -> "PromptComposerStyleEntities":
        for name, key in (
            ("characters", "char_id"),
            ("locations", "loc_id"),
            ("recurring_props", "prop_id"),
            ("recurring_visual_subjects", "subject_id"),
            ("motifs", "motif_id"),
        ):
            values = [str(getattr(item, key)) for item in getattr(self, name)]
            if len(values) != len(set(values)):
                raise ValueError(f"{name} IDs must be unique")
        return self

class PromptReferenceBinding(StrictModel):
    anchor_type: Literal["character", "location", "prop", "recurring_visual_subject"]
    anchor_id: Annotated[str, Field(pattern=r"^(char|loc|prop|subject)_[0-9]{3}$")]
    reference_artifact_id: ArtifactId
    media_artifact_id: ArtifactId
    approval_set_artifact_id: ArtifactId
    source_definition_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    media_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mime_type: NonEmptyText
    image_artifact_id: ArtifactId


class PromptComposerVisualBeat(StrictModel):
    scene_id: SceneId
    beat_id: BeatId
    visual_context_ref: VisualContextId
    beat_order: PositiveInt
    local_beat_order: PositiveInt
    continuity_group_id: ContinuityGroupId
    visual_priority: VisualPriority
    is_narrative_marker: bool
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
    framing: Literal["extreme_wide", "wide", "medium_wide", "medium", "medium_close", "close_up", "extreme_close_up", "overhead", "insert", "graphic_layout"]
    camera_angle: Literal["eye_level", "low_angle", "high_angle", "top_down", "dutch_angle", "pov", "neutral_graphic"]
    camera_movement: Literal["static", "slow_push_in", "slow_pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "tracking", "subtle_handheld", "parallax", "none"]
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
    selected_reference_bindings: list[PromptReferenceBinding]

    @model_validator(mode="after")
    def validate_refs(self) -> "PromptComposerVisualBeat":
        if self.continuity_with_previous == "new_setup" and self.previous_beat_image_reference_required:
            raise ValueError("new_setup must not require the previous Beat image")
        if self.continuity_with_previous == "match_composition" and not self.previous_beat_image_reference_required:
            raise ValueError("match_composition must require the previous Beat image")
        for name in (
            "selected_character_refs", "selected_location_refs", "selected_prop_refs", "selected_recurring_visual_subject_refs",
            "selected_motif_refs", "selected_native_visual_reference_ids", "negative_requirements", "visible_text",
        ):
            values = list(getattr(self, name))
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        seen: set[tuple[str, str]] = set()
        binding_refs = [binding.reference_artifact_id for binding in self.selected_reference_bindings]
        if set(binding_refs) != set(self.selected_native_visual_reference_ids):
            raise ValueError("selected reference bindings must match selected native visual references")
        for binding in self.selected_reference_bindings:
            key = (binding.anchor_type, binding.anchor_id)
            if key in seen:
                raise ValueError("reference bindings must be unique by anchor")
            seen.add(key)
            if binding.anchor_type == "character" and binding.anchor_id not in self.selected_character_refs:
                raise ValueError("character binding must reference a selected character")
            if binding.anchor_type == "location" and binding.anchor_id not in self.selected_location_refs:
                raise ValueError("location binding must reference a selected location")
            if binding.anchor_type == "prop" and binding.anchor_id not in self.selected_prop_refs:
                raise ValueError("prop binding must reference a selected prop")
            if binding.anchor_type == "recurring_visual_subject" and binding.anchor_id not in self.selected_recurring_visual_subject_refs:
                raise ValueError("recurring subject binding must reference a selected recurring subject")
        return self


class PromptComposerInput(StrictModel):
    schema_version: Literal["3.0"]
    project_id: ProjectId
    source_script_revision_id: ArtifactId
    source_script_analysis_revision_id: ArtifactId
    source_style_bible_revision_id: ArtifactId
    source_scene_plan_revision_id: ArtifactId
    source_visual_plan_revision_id: ArtifactId
    source_visual_reference_approval_set_artifact_id: ArtifactId | None
    prompt_bundle_revision_id: ArtifactId
    options: PromptComposerOptions
    visual_style_snapshot: VisualStyleSnapshotModel
    style_entities: PromptComposerStyleEntities
    visual_context_catalog: dict[VisualContextId, PromptComposerVisualContext]
    visual_beats: list[PromptComposerVisualBeat]

    @model_validator(mode="after")
    def validate_order(self) -> "PromptComposerInput":
        beat_ids = [beat.beat_id for beat in self.visual_beats]
        beat_orders = [beat.beat_order for beat in self.visual_beats]
        if len(beat_ids) != len(set(beat_ids)):
            raise ValueError("visual beat IDs must be unique")
        if len(beat_orders) != len(set(beat_orders)) or beat_orders != sorted(beat_orders):
            raise ValueError("visual beats must preserve unique global beat order")
        character_ids = {item.char_id for item in self.style_entities.characters}
        location_ids = {item.loc_id for item in self.style_entities.locations}
        prop_ids = {item.prop_id for item in self.style_entities.recurring_props}
        recurring_subject_ids = {item.subject_id for item in self.style_entities.recurring_visual_subjects}
        motif_ids = {item.motif_id for item in self.style_entities.motifs}
        for beat in self.visual_beats:
            if beat.visual_context_ref not in self.visual_context_catalog:
                raise ValueError("visual beat references an unknown visual context")
            if any(ref not in character_ids for ref in beat.selected_character_refs):
                raise ValueError("visual beat references an unknown character")
            if any(ref not in location_ids for ref in beat.selected_location_refs):
                raise ValueError("visual beat references an unknown location")
            if any(ref not in prop_ids for ref in beat.selected_prop_refs):
                raise ValueError("visual beat references an unknown recurring prop")
            if any(ref not in recurring_subject_ids for ref in beat.selected_recurring_visual_subject_refs):
                raise ValueError("visual beat references an unknown recurring visual subject")
            if any(ref not in motif_ids for ref in beat.selected_motif_refs):
                raise ValueError("visual beat references an unknown motif")
            if self.source_visual_reference_approval_set_artifact_id is None:
                if beat.selected_reference_bindings:
                    raise ValueError("reference bindings require an approval-set identity")
            elif any(binding.approval_set_artifact_id != self.source_visual_reference_approval_set_artifact_id for binding in beat.selected_reference_bindings):
                raise ValueError("reference bindings must come from the exact source approval set")
        return self


class ValidationIssue(StrictModel):
    path: NonEmptyText
    code: Annotated[str, Field(pattern=r"^[A-Z0-9_]+$")]
    message: NonEmptyText


class PromptBundleValidationReport(StrictModel):
    lineage_status: QCStatus
    beat_coverage_status: QCStatus
    order_status: QCStatus
    source_identity_status: QCStatus
    referential_integrity_status: QCStatus
    prompt_content_status: QCStatus
    negative_prompt_status: QCStatus
    generation_options_status: QCStatus
    reference_binding_status: QCStatus
    prompt_hash_status: QCStatus
    forbidden_field_status: QCStatus
    missing_prompt_beat_ids: list[BeatId]
    duplicate_prompt_beat_ids: list[BeatId]
    unexpected_prompt_beat_ids: list[BeatId]
    order_errors: list[ValidationIssue]
    unknown_reference_errors: list[ValidationIssue]
    prompt_content_errors: list[ValidationIssue]
    negative_prompt_errors: list[ValidationIssue]
    generation_option_errors: list[ValidationIssue]
    reference_binding_errors: list[ValidationIssue]
    prompt_hash_errors: list[ValidationIssue]
    forbidden_field_errors: list[ValidationIssue]
    warnings: list[NonEmptyText]


class PromptSourceProvenance(StrictModel):
    representation_family: RepresentationFamily
    selected_character_refs: list[CharacterId]
    selected_location_refs: list[LocationId]
    selected_prop_refs: list[PropId]
    selected_recurring_visual_subject_refs: list[RecurringSubjectId]
    selected_motif_refs: list[MotifId]
    selected_native_visual_reference_ids: list[ArtifactId]


class PromptContinuityReferenceRequirement(StrictModel):
    reference_tag: Literal["prev_beat"]
    source_beat_id: BeatId
    relation: ContinuityDependencyRelation

    @model_validator(mode="after")
    def validate_relation(self) -> "PromptContinuityReferenceRequirement":
        if self.relation == "new_setup":
            raise ValueError("continuity reference requirement cannot use new_setup")
        return self


class PromptBundleItem(StrictModel):
    prompt_item_id: PromptItemId
    scene_id: SceneId
    beat_id: BeatId
    beat_order: PositiveInt
    local_beat_order: PositiveInt
    continuity_group_id: ContinuityGroupId
    visual_priority: VisualPriority
    target_asset_role: Literal["beat_visual"]
    prompt_origin: Literal["compiled", "manual_edit"]
    image_prompt: NonEmptyText
    negative_prompt: NonEmptyText
    prompt_hash: PromptHash
    generation_options: PromptComposerOptions
    reference_bindings: list[PromptReferenceBinding]
    continuity_reference_requirement: PromptContinuityReferenceRequirement | None
    source_provenance: PromptSourceProvenance


class PromptBundleModel(StrictModel):
    artifact_type: Literal["prompt_bundle"]
    schema_version: Literal["3.0"]
    project_id: ProjectId
    prompt_bundle_revision_id: ArtifactId
    parent_prompt_bundle_revision_id: ArtifactId | None
    revision_operation: Literal["automatic_compilation", "full_compilation", "beat_recompilation", "manual_edit", "approval"]
    operation_target_beat_id: BeatId | None = None
    source_script_revision_id: ArtifactId
    source_script_analysis_revision_id: ArtifactId
    source_style_bible_revision_id: ArtifactId
    source_scene_plan_revision_id: ArtifactId
    source_visual_plan_revision_id: ArtifactId
    source_visual_reference_approval_set_artifact_id: ArtifactId | None
    status: Literal["draft_ready", "approved"]
    options: PromptComposerOptions
    source_beat_count: NonNegativeInt
    prompt_item_count: NonNegativeInt
    prompt_items: list[PromptBundleItem]
    backend_qc: PromptBundleValidationReport

    @model_validator(mode="after")
    def validate_bundle(self) -> "PromptBundleModel":
        if self.revision_operation == "automatic_compilation":
            if self.status != "approved" or self.parent_prompt_bundle_revision_id is not None or self.operation_target_beat_id is not None:
                raise ValueError("automatic compilation must be an approved root revision")
        elif self.revision_operation == "full_compilation":
            if self.status != "draft_ready" or self.operation_target_beat_id is not None:
                raise ValueError("full compilation must be draft_ready and not target a beat")
        else:
            if self.parent_prompt_bundle_revision_id is None:
                raise ValueError("child Prompt Bundle revision requires a parent")
            expected = "approved" if self.revision_operation in {"approval", "beat_recompilation", "manual_edit"} else "draft_ready"
            if self.status != expected:
                raise ValueError("revision operation and status disagree")
            if self.revision_operation in {"beat_recompilation", "manual_edit"} and self.operation_target_beat_id is None:
                raise ValueError("beat mutation requires a target beat")
            if self.revision_operation == "approval" and self.operation_target_beat_id is not None:
                raise ValueError("approval must not target a beat")
        if self.prompt_item_count != len(self.prompt_items) or self.source_beat_count != len(self.prompt_items):
            raise ValueError("prompt counts must equal prompt items")
        prompt_ids = [item.prompt_item_id for item in self.prompt_items]
        beat_ids = [item.beat_id for item in self.prompt_items]
        if len(prompt_ids) != len(set(prompt_ids)) or len(beat_ids) != len(set(beat_ids)):
            raise ValueError("prompt identities must be unique")
        if [item.beat_order for item in self.prompt_items] != sorted(item.beat_order for item in self.prompt_items):
            raise ValueError("prompt items must preserve source order")
        for item in self.prompt_items:
            if item.generation_options != self.options:
                raise ValueError("prompt item generation options must equal bundle options")
            for binding in item.reference_bindings:
                if self.source_visual_reference_approval_set_artifact_id is None:
                    raise ValueError("approved reference bindings require a source approval-set identity")
                if binding.approval_set_artifact_id != self.source_visual_reference_approval_set_artifact_id:
                    raise ValueError("reference bindings must come from the exact source approval set")
        critical_statuses = [value for key, value in self.backend_qc.model_dump(mode="json").items() if key.endswith("_status")]
        if any(value != "passed" for value in critical_statuses):
            raise ValueError("all critical Prompt Bundle QA statuses must pass")
        return self


class PromptBundleReadResponse(StrictModel):
    artifact_id: ArtifactId
    artifact_type: Literal["prompt_bundle"]
    project_id: ProjectId
    source_visual_plan_revision_id: ArtifactId
    status: Literal["draft_ready", "approved"]
    content: PromptBundleModel

    @model_validator(mode="after")
    def wrapper_identity(self) -> "PromptBundleReadResponse":
        if self.artifact_id != self.content.prompt_bundle_revision_id:
            raise ValueError("artifact_id must equal content.prompt_bundle_revision_id")
        if self.project_id != self.content.project_id:
            raise ValueError("project_id must equal content.project_id")
        if self.source_visual_plan_revision_id != self.content.source_visual_plan_revision_id:
            raise ValueError("source Visual Plan identity must match")
        if self.status != self.content.status:
            raise ValueError("status must match content.status")
        return self


class PromptBundleLineage(StrictModel):
    """Optimistic consumer pointers supplied by Prompt Composer callers.

    All deeper Script / Script Analysis / Scene Plan lineage is read from the
    approved Visual Plan and checked against the current Project by the backend.
    """

    style_bible_artifact_id: ArtifactId
    visual_plan_artifact_id: ArtifactId
    expected_prompt_bundle_artifact_id: ArtifactId | None


class PromptBundleCompileRequest(PromptBundleLineage):
    options: PromptComposerOptions


class PromptBundleRecompileRequest(PromptBundleLineage):
    options: PromptComposerOptions


class PromptBundleBeatRecompileRequest(PromptBundleLineage):
    pass


class PromptBundleManualEditRequest(PromptBundleLineage):
    image_prompt: NonEmptyText
    negative_prompt: NonEmptyText


class PromptBundleApproveRequest(PromptBundleLineage):
    pass


class PromptBundleRevisionResponse(StrictModel):
    artifact_id: ArtifactId
    artifact: PromptBundleModel

    @model_validator(mode="after")
    def revision_identity(self) -> "PromptBundleRevisionResponse":
        if self.artifact_id != self.artifact.prompt_bundle_revision_id:
            raise ValueError("artifact_id must equal prompt_bundle_revision_id")
        return self


class PromptBundleProjectReadResponse(StrictModel):
    project_id: ProjectId
    generated: bool
    artifact_id: ArtifactId | None = None
    artifact: PromptBundleModel | None = None

    @model_validator(mode="after")
    def read_identity(self) -> "PromptBundleProjectReadResponse":
        if not self.generated:
            if self.artifact_id is not None or self.artifact is not None:
                raise ValueError("generated=false requires null artifact_id and artifact")
            return self
        if self.artifact_id is None or self.artifact is None:
            raise ValueError("generated=true requires artifact_id and artifact")
        if self.artifact_id != self.artifact.prompt_bundle_revision_id:
            raise ValueError("artifact_id must equal prompt_bundle_revision_id")
        if self.project_id != self.artifact.project_id:
            raise ValueError("project_id must equal artifact.project_id")
        return self
