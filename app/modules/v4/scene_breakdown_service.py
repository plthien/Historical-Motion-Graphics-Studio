from __future__ import annotations

from copy import deepcopy
import json
import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.core.hashing import canonical_json_hash
from app.db.enums import ArtifactType
from app.db.models import Project
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.platform.artifact_loader import ProjectArtifactLoader
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.beat_timing_map import compile_beat_timing_map, validate_word_timing_authority
from app.modules.v4.production_artifact_loader import ProductionArtifactLoaderV4
from app.modules.v4.scene_breakdown_input_builder import build_section_input, extract_ordered_sections
from app.modules.v4.prompt_composer_service import PromptComposerService
from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService
from app.modules.v4.scene_asset_working_state import SceneAssetWorkingStateService
from app.modules.v4.scene_breakdown_loader import SceneBreakdownArtifactLoaderV4
from app.modules.v4.scene_breakdown_validator import (
    SceneBreakdownValidationError,
    validate_and_normalize_section,
    validate_reusable_section_fragment,
)
from app.modules.v4.script_analysis_loader import ScriptAnalysisArtifactLoader
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.modules.v4.style_bible_loader import StyleBibleArtifactLoaderV4
from app.modules.v4.visual_reference_loader import VisualReferenceArtifactLoaderV4
from app.modules.v4.visual_plan_loader import VisualPlanArtifactLoaderV4
from app.modules.v4.scene_plan_assembler import (
    approve_scene_plan,
    assemble_scene_plan,
)
from app.modules.v4.visual_plan_assembler import (
    approve_visual_plan_for_scene_plan,
    assemble_visual_plan_from_scene_breakdown,
    combined_fragments_from_paired_plans,
)
from app.modules.v4.scene_breakdown_review import (
    editable_section_from_paired_plans,
    fixed_shape_issues,
    raw_output_from_editable,
    source_ownership_issues,
)
from app.repositories import (
    ArtifactRepository,
    ProjectRepository,
    SceneVisualPlanCommitGuard,
    SceneVisualPromptBundleCommitGuard,
)
from app.services.artifacts import (
    ArtifactService,
    StagedArtifact,
    cleanup_staged_artifact_safely,
    rollback_safely,
)

logger = logging.getLogger(__name__)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


class SceneBreakdownServiceV4:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifact_repository = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifact_repository)
        self.prompt_revisions = SceneAssetPromptRevisionService(session)
        self.scene_asset_working_state = SceneAssetWorkingStateService(session)
        self.v3 = PlatformContractValidator()
        self.v4 = V4ContractValidator()
        self.project_loader = ProjectArtifactLoader(self.artifacts, self.v3)
        self.analysis_loader = ScriptAnalysisArtifactLoader(
            self.artifacts,
            self.v4,
            ScriptAnalysisSemanticValidator(),
        )
        self.style_loader = StyleBibleArtifactLoaderV4(self.artifacts)
        self.loader = SceneBreakdownArtifactLoaderV4(
            self.artifacts,
            self.artifact_repository,
            self.v4,
        )
        self.visual_plan_loader = VisualPlanArtifactLoaderV4(self.artifacts)
        self.visual_reference_loader = VisualReferenceArtifactLoaderV4(self.artifacts)
        self.production_loader = ProductionArtifactLoaderV4(self.artifacts, self.v4)

    def _project(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError(
                "PROJECT_NOT_FOUND",
                f"Project '{project_id}' was not found.",
                404,
                retryable=False,
            )
        return project

    def approved_voice_timing(self, project: Project):
        if project.narration_audio_artifact_id is None or project.narration_word_timing_map_artifact_id is None:
            raise AppError("SCENE_BREAKDOWN_VOICE_NOT_APPROVED", "Scene Breakdown requires an approved Narration Audio + NarrationWordTimingMap pair.", 409, retryable=False)
        audio = self.production_loader.load_approved_narration_audio(project)
        timing = self.production_loader.load_narration_word_timing_map(project, str(project.narration_word_timing_map_artifact_id), require_current=True)
        validate_word_timing_authority(
            project_id=project.project_id,
            script_artifact_id=str(project.script_artifact_id),
            narration_audio_artifact_id=str(project.narration_audio_artifact_id),
            narration_word_timing_map_artifact_id=str(project.narration_word_timing_map_artifact_id),
            word_timing_map=timing,
        )
        return audio, timing

    def validate_current_lineage(
        self,
        project: Project,
        request: dict[str, Any],
        *,
        require_scene_plan: bool = False,
        validate_current_pair: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        checks = (
            ("script_artifact_id", "SCENE_BREAKDOWN_SCRIPT_NOT_CURRENT", "Script"),
            ("script_analysis_artifact_id", "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_NOT_CURRENT", "Script Analysis"),
            ("style_bible_artifact_id", "SCENE_BREAKDOWN_STYLE_BIBLE_NOT_CURRENT", "Style Bible"),
        )
        for field, code, label in checks:
            if getattr(project, field) != request.get(field):
                raise AppError(code, f"The requested {label} artifact is not current.", 409, retryable=False, details={field: request.get(field)})
        expected_scene = request.get("expected_scene_plan_artifact_id")
        expected_visual = request.get("expected_visual_plan_artifact_id")
        if project.scene_plan_artifact_id != expected_scene:
            raise AppError("SCENE_PLAN_REVISION_CONFLICT", "The expected Scene Plan pointer is no longer current.", 409, retryable=False, details={"expected_scene_plan_artifact_id": expected_scene})
        if project.visual_plan_artifact_id != expected_visual:
            raise AppError("VISUAL_PLAN_REVISION_CONFLICT", "The expected Visual Plan pointer is no longer current.", 409, retryable=False, details={"expected_visual_plan_artifact_id": expected_visual})
        if (expected_scene is None) != (expected_visual is None):
            raise AppError("SCENE_VISUAL_PLAN_PAIR_INVALID", "Scene Plan and Visual Plan pointers must be absent or present as one pair.", 409, retryable=False)
        if require_scene_plan and expected_scene is None:
            raise AppError("SCENE_PLAN_NOT_FOUND", "A current paired Scene Plan and Visual Plan are required.", 404, retryable=False)
        script = self.project_loader.load_script(project, str(request["script_artifact_id"]), require_current=True, require_downstream_ready=True)
        analysis = self.analysis_loader.load(project=project, artifact_id=str(request["script_analysis_artifact_id"]), script_artifact=script, expected_script_revision_id=str(request["script_artifact_id"]), require_current=True)
        style = self.style_loader.load(project=project, artifact_id=str(request["style_bible_artifact_id"]), script_analysis=analysis, require_current=True)
        if style.get("status") != "text_approved":
            raise AppError("SCENE_BREAKDOWN_STYLE_BIBLE_NOT_TEXT_APPROVED", "The current Style Bible must be text-approved.", 409, retryable=False)
        lineage = style.get("lineage", {})
        if lineage.get("script_analysis_artifact_id") != request["script_analysis_artifact_id"] or lineage.get("source_script_artifact_id") != request["script_artifact_id"]:
            raise AppError("SCENE_BREAKDOWN_UPSTREAM_STALE", "Style Bible lineage does not match current Script Analysis.", 409, retryable=False)
        self.approved_voice_timing(project)
        if expected_scene is not None and validate_current_pair:
            scene_plan = self.loader.load_scene_plan(project, expected_scene, require_current=True)
            visual_plan = self.visual_plan_loader.load_visual_plan(project, expected_visual, require_current=True)
            if visual_plan["source_scene_plan_revision_id"] != scene_plan["scene_plan_revision_id"]:
                raise AppError("SCENE_VISUAL_PLAN_PAIR_INVALID", "Current Scene Plan and Visual Plan are not an exact pair.", 409, retryable=False)
        return script, analysis, style

    def get_current_scene_plan(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        scene_id = project.scene_plan_artifact_id
        visual_id = project.visual_plan_artifact_id
        if (scene_id is None) != (visual_id is None):
            raise AppError("SCENE_VISUAL_PLAN_PAIR_INVALID", "Current Scene Plan and Visual Plan pointers are incomplete.", 409, retryable=False)
        if scene_id is None:
            response = {"project_id": project_id, "generated": False, "scene_plan_artifact_id": None, "scene_plan": None, "visual_plan_artifact_id": None, "visual_plan": None}
        else:
            scene_plan = self.loader.load_scene_plan(project, scene_id, require_current=True)
            visual_plan = self.visual_plan_loader.load_visual_plan(project, str(visual_id), require_current=True)
            if visual_plan["source_scene_plan_revision_id"] != scene_id:
                raise AppError("SCENE_VISUAL_PLAN_PAIR_INVALID", "Current Scene Plan and Visual Plan are not paired.", 409, retryable=False)
            response = {"project_id": project_id, "generated": True, "scene_plan_artifact_id": scene_id, "scene_plan": scene_plan, "visual_plan_artifact_id": visual_id, "visual_plan": visual_plan}
        return self.v4.validate("scene_plan_read_response_v1.schema.json", response, error_code="SCENE_PLAN_READ_RESPONSE_INVALID")

    def get_current_visual_plan(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        artifact_id = project.visual_plan_artifact_id
        if artifact_id is None:
            response = {"project_id": project_id, "generated": False, "artifact_id": None, "artifact": None}
        else:
            visual_plan = self.visual_plan_loader.load_visual_plan(project, artifact_id, require_current=True)
            response = {"project_id": project_id, "generated": True, "artifact_id": artifact_id, "artifact": visual_plan}
        return self.v4.validate("visual_plan_read_response_v1.schema.json", response, error_code="VISUAL_PLAN_READ_RESPONSE_INVALID")

    def _mark_artifacts_current(self, *staged_artifacts: StagedArtifact) -> None:
        for staged in staged_artifacts:
            previous = self.artifact_repository.get_current_by_logical_id(staged.logical_id)
            if previous is not None and previous.artifact_id != staged.artifact_id:
                previous.is_current = False
                previous.superseded_by_artifact_id = staged.artifact_id
            staged.artifact.is_current = True
            staged.artifact.superseded_by_artifact_id = None

    def _mark_pair_current(self, staged_scene: StagedArtifact, staged_visual: StagedArtifact) -> None:
        self._mark_artifacts_current(staged_scene, staged_visual)

    @staticmethod
    def _scene_by_beat(scene_plan: dict[str, Any]) -> dict[str, str]:
        return {
            str(beat["beat_id"]): str(scene["scene_id"])
            for scene in scene_plan.get("scenes", [])
            for beat in scene.get("beats", [])
        }

    def _invalidate_scene_assets_for_pair(self, project_id: str, scene_plan: dict[str, Any], *, now_iso: str) -> int:
        return self.scene_asset_working_state.invalidate_for_lineage_replacement(
            project_id=project_id,
            scene_by_beat=self._scene_by_beat(scene_plan),
            now_iso=now_iso,
        )

    def publish_scene_visual_plan(
        self,
        *,
        project_id: str,
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        guard: SceneVisualPlanCommitGuard,
        source_job_id: str | None,
    ) -> tuple[str, str]:
        staged: list[StagedArtifact] = []
        try:
            self.v4.validate("scene_plan_v1.schema.json", scene_plan, error_code="SCENE_PLAN_INVALID")
            self.v4.validate("visual_plan_v1.schema.json", visual_plan, error_code="VISUAL_PLAN_INVALID")
            if visual_plan["source_scene_plan_revision_id"] != scene_plan["scene_plan_revision_id"]:
                raise AppError("SCENE_VISUAL_PLAN_PAIR_INVALID", "Visual Plan does not reference the Scene Plan being published.", 409, retryable=False)
            scene_staged = self.artifacts.stage_artifact_revision(logical_id=f"project/{project_id}/scene_plan", artifact_type=ArtifactType.JSON, filename="scene_plan.json", content=_json_bytes(scene_plan), artifact_id=scene_plan["scene_plan_revision_id"], source_job_id=source_job_id, provider_source="deterministic", content_hash=guard.script_analysis_artifact_id)
            staged.append(scene_staged)
            visual_staged = self.artifacts.stage_artifact_revision(logical_id=f"project/{project_id}/visual_plan", artifact_type=ArtifactType.JSON, filename="visual_plan.json", content=_json_bytes(visual_plan), artifact_id=visual_plan["visual_plan_revision_id"], source_job_id=source_job_id, provider_source="deterministic", content_hash=guard.script_analysis_artifact_id)
            staged.append(visual_staged)
            publication_time = utc_now_iso()
            self._invalidate_scene_assets_for_pair(project_id, scene_plan, now_iso=publication_time)
            won = self.projects.compare_and_set_scene_visual_plan_v4(project_id=project_id, new_scene_plan_artifact_id=scene_plan["scene_plan_revision_id"], new_visual_plan_artifact_id=visual_plan["visual_plan_revision_id"], guard=guard, updated_at=publication_time)
            if not won:
                raise AppError("SCENE_VISUAL_PLAN_REVISION_CONFLICT", "Scene Plan, Visual Plan or upstream lineage changed before paired publication.", 409, retryable=False)
            self._mark_pair_current(scene_staged, visual_staged)
            self.session.flush()
            self.session.commit()
            return scene_plan["scene_plan_revision_id"], visual_plan["visual_plan_revision_id"]
        except Exception:
            rollback_safely(self.session, logger=logger)
            for item in staged:
                cleanup_staged_artifact_safely(item, logger=logger)
            raise

    def publish_approved_scene_visual_prompt_bundle(
        self,
        *,
        project_id: str,
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        prompt_bundle: dict[str, Any],
        beat_timing_map: dict[str, Any],
        beat_timing_map_artifact_id: str,
        guard: SceneVisualPromptBundleCommitGuard,
    ) -> tuple[str, str, str, str]:
        staged: list[StagedArtifact] = []
        try:
            self.v4.validate("scene_plan_v1.schema.json", scene_plan, error_code="SCENE_PLAN_INVALID")
            self.v4.validate("visual_plan_v1.schema.json", visual_plan, error_code="VISUAL_PLAN_INVALID")
            self.v4.validate("prompt_bundle_v1.schema.json", prompt_bundle, error_code="PROMPT_BUNDLE_VALIDATION_FAILED")
            self.v4.validate("beat_timing_map_v1.schema.json", beat_timing_map, error_code="BEAT_TIMING_MAP_INVALID")
            if scene_plan.get("status") != "approved" or visual_plan.get("status") != "approved":
                raise AppError(
                    "SCENE_VISUAL_PLAN_STATUS_INVALID",
                    "Atomic approval requires approved Scene Plan and Visual Plan candidates.",
                    409,
                    retryable=False,
                )
            if prompt_bundle.get("status") != "approved" or prompt_bundle.get("revision_operation") != "automatic_compilation":
                raise AppError(
                    "PROMPT_BUNDLE_STATUS_INVALID",
                    "Scene Breakdown approval requires an automatically compiled approved Prompt Bundle.",
                    409,
                    retryable=False,
                )
            if visual_plan["source_scene_plan_revision_id"] != scene_plan["scene_plan_revision_id"]:
                raise AppError(
                    "SCENE_VISUAL_PLAN_PAIR_INVALID",
                    "Visual Plan does not reference the approved Scene Plan candidate.",
                    409,
                    retryable=False,
                )
            expected_prompt_lineage = {
                "source_script_revision_id": scene_plan["source_script_revision_id"],
                "source_script_analysis_revision_id": scene_plan["source_script_analysis_revision_id"],
                "source_style_bible_revision_id": scene_plan["source_style_bible_revision_id"],
                "source_scene_plan_revision_id": scene_plan["scene_plan_revision_id"],
                "source_visual_plan_revision_id": visual_plan["visual_plan_revision_id"],
                "source_visual_reference_approval_set_artifact_id": guard.visual_reference_approval_set_artifact_id,
            }
            for field, expected in expected_prompt_lineage.items():
                if prompt_bundle.get(field) != expected:
                    raise AppError(
                        "PROMPT_BUNDLE_UPSTREAM_STALE",
                        "Automatically compiled Prompt Bundle lineage does not match the approved plan candidates.",
                        409,
                        retryable=False,
                        details={"field": field},
                    )

            scene_staged = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project_id}/scene_plan",
                artifact_type=ArtifactType.JSON,
                filename="scene_plan.json",
                content=_json_bytes(scene_plan),
                artifact_id=scene_plan["scene_plan_revision_id"],
                source_job_id=None,
                provider_source="deterministic",
                content_hash=guard.script_analysis_artifact_id,
            )
            staged.append(scene_staged)
            visual_staged = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project_id}/visual_plan",
                artifact_type=ArtifactType.JSON,
                filename="visual_plan.json",
                content=_json_bytes(visual_plan),
                artifact_id=visual_plan["visual_plan_revision_id"],
                source_job_id=None,
                provider_source="deterministic",
                content_hash=guard.script_analysis_artifact_id,
            )
            staged.append(visual_staged)
            prompt_staged = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project_id}/prompt_bundle",
                artifact_type=ArtifactType.JSON,
                filename="prompt_bundle.json",
                content=_json_bytes(prompt_bundle),
                artifact_id=prompt_bundle["prompt_bundle_revision_id"],
                source_job_id=None,
                provider_source="deterministic",
            )
            staged.append(prompt_staged)
            beat_timing_staged = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project_id}/beat_timing_map",
                artifact_type=ArtifactType.JSON,
                filename="beat_timing_map.json",
                content=_json_bytes(beat_timing_map),
                artifact_id=beat_timing_map_artifact_id,
                source_job_id=None,
                provider_source="deterministic",
            )
            staged.append(beat_timing_staged)
            self._after_scene_visual_prompt_atomic_stage(
                scene_plan_artifact=scene_staged,
                visual_plan_artifact=visual_staged,
                prompt_bundle_artifact=prompt_staged,
            )

            publication_time = utc_now_iso()
            self._invalidate_scene_assets_for_pair(project_id, scene_plan, now_iso=publication_time)
            won = self.projects.compare_and_set_scene_visual_prompt_bundle_v4(
                project_id=project_id,
                new_scene_plan_artifact_id=scene_plan["scene_plan_revision_id"],
                new_visual_plan_artifact_id=visual_plan["visual_plan_revision_id"],
                new_prompt_bundle_artifact_id=prompt_bundle["prompt_bundle_revision_id"],
                new_beat_timing_map_artifact_id=beat_timing_map_artifact_id,
                guard=guard,
                updated_at=publication_time,
            )
            if not won:
                raise self._classify_scene_visual_prompt_publication_conflict(project_id, guard)
            self._mark_artifacts_current(scene_staged, visual_staged, prompt_staged, beat_timing_staged)
            self.prompt_revisions.publish_approved_bundle(project_id, prompt_bundle)
            self.session.flush()
            self.session.commit()
            return (
                scene_plan["scene_plan_revision_id"],
                visual_plan["visual_plan_revision_id"],
                prompt_bundle["prompt_bundle_revision_id"],
                beat_timing_map_artifact_id,
            )
        except Exception:
            rollback_safely(self.session, logger=logger)
            for item in staged:
                cleanup_staged_artifact_safely(item, logger=logger)
            raise

    def _after_scene_visual_prompt_atomic_stage(
        self,
        *,
        scene_plan_artifact: StagedArtifact,
        visual_plan_artifact: StagedArtifact,
        prompt_bundle_artifact: StagedArtifact,
    ) -> None:
        return None

    def _classify_scene_visual_prompt_publication_conflict(
        self,
        project_id: str,
        guard: SceneVisualPromptBundleCommitGuard,
    ) -> AppError:
        self.session.expire_all()
        current = self.projects.get(project_id)
        if current is None:
            return AppError("PROJECT_NOT_FOUND", "Project was not found.", 404, retryable=False)
        if (
            current.script_artifact_id != guard.script_artifact_id
            or current.script_analysis_artifact_id != guard.script_analysis_artifact_id
            or current.style_bible_artifact_id != guard.style_bible_artifact_id
            or current.style_bible_visual_reference_approval_set_artifact_id
            != guard.visual_reference_approval_set_artifact_id
        ):
            return AppError(
                "SCENE_BREAKDOWN_UPSTREAM_STALE",
                "Scene Breakdown upstream lineage changed before atomic approval publication.",
                409,
                retryable=False,
            )
        if (
            current.narration_audio_artifact_id != guard.narration_audio_artifact_id
            or current.narration_word_timing_map_artifact_id != guard.narration_word_timing_map_artifact_id
        ):
            return AppError(
                "SCENE_BREAKDOWN_VOICE_STALE",
                "Approved Voice changed before atomic Scene approval publication.",
                409,
                retryable=False,
            )
        return AppError(
            "SCENE_VISUAL_PROMPT_BUNDLE_REVISION_CONFLICT",
            "Scene Plan, Visual Plan, or Prompt Bundle pointer changed before atomic approval publication.",
            409,
            retryable=False,
        )

    def _after_scene_breakdown_atomic_stage(self, *, run_artifact: StagedArtifact, scene_plan_artifact: StagedArtifact, visual_plan_artifact: StagedArtifact) -> None:
        return None

    def publish_scene_visual_plan_with_completed_run(
        self,
        *,
        project_id: str,
        run_artifact_id: str,
        run_payload: dict[str, Any],
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        guard: SceneVisualPlanCommitGuard,
        source_job_id: str | None,
        before_commit: Callable[[], None] | None = None,
    ) -> tuple[str, str]:
        staged: list[StagedArtifact] = []
        try:
            if run_payload.get("status") != "completed":
                raise AppError("SCENE_BREAKDOWN_RUN_INVALID", "Only a completed run can publish paired plans.", 500, retryable=False)
            self.v4.validate("scene_breakdown_run_v1.schema.json", run_payload, error_code="SCENE_BREAKDOWN_RUN_INVALID")
            self.v4.validate("scene_plan_v1.schema.json", scene_plan, error_code="SCENE_PLAN_INVALID")
            self.v4.validate("visual_plan_v1.schema.json", visual_plan, error_code="VISUAL_PLAN_INVALID")
            if visual_plan["source_scene_plan_revision_id"] != scene_plan["scene_plan_revision_id"]:
                raise AppError("SCENE_VISUAL_PLAN_PAIR_INVALID", "Visual Plan does not reference the Scene Plan being published.", 409, retryable=False)
            run_staged = self.artifacts.write_artifact(logical_id=f"project/{run_payload['project_id']}/scene_breakdown_v4/runs/{run_payload['run_id']}", artifact_type=ArtifactType.JSON, filename="scene_breakdown_run.json", content=_json_bytes(run_payload), artifact_id=run_artifact_id, source_job_id=source_job_id)
            staged.append(run_staged)
            scene_staged = self.artifacts.stage_artifact_revision(logical_id=f"project/{project_id}/scene_plan", artifact_type=ArtifactType.JSON, filename="scene_plan.json", content=_json_bytes(scene_plan), artifact_id=scene_plan["scene_plan_revision_id"], source_job_id=source_job_id, provider_source="deterministic", content_hash=guard.script_analysis_artifact_id)
            staged.append(scene_staged)
            visual_staged = self.artifacts.stage_artifact_revision(logical_id=f"project/{project_id}/visual_plan", artifact_type=ArtifactType.JSON, filename="visual_plan.json", content=_json_bytes(visual_plan), artifact_id=visual_plan["visual_plan_revision_id"], source_job_id=source_job_id, provider_source="deterministic", content_hash=guard.script_analysis_artifact_id)
            staged.append(visual_staged)
            self._after_scene_breakdown_atomic_stage(run_artifact=run_staged, scene_plan_artifact=scene_staged, visual_plan_artifact=visual_staged)
            publication_time = utc_now_iso()
            self._invalidate_scene_assets_for_pair(project_id, scene_plan, now_iso=publication_time)
            won = self.projects.compare_and_set_scene_visual_plan_v4(project_id=project_id, new_scene_plan_artifact_id=scene_plan["scene_plan_revision_id"], new_visual_plan_artifact_id=visual_plan["visual_plan_revision_id"], guard=guard, updated_at=publication_time)
            if not won:
                raise AppError("SCENE_VISUAL_PLAN_REVISION_CONFLICT", "Paired plan or upstream lineage changed before publication.", 409, retryable=False)
            self._mark_pair_current(scene_staged, visual_staged)
            if before_commit is not None:
                before_commit()
            self.session.flush()
            self.session.commit()
            return scene_plan["scene_plan_revision_id"], visual_plan["visual_plan_revision_id"]
        except Exception:
            rollback_safely(self.session, logger=logger)
            for item in staged:
                cleanup_staged_artifact_safely(item, logger=logger)
            raise

    def _load_edit_context(
        self,
        *,
        project: Project,
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        section_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
        if scene_plan.get("status") != "draft_ready" or visual_plan.get("status") != "draft_ready":
            raise AppError("SCENE_VISUAL_PLAN_STATUS_INVALID", "Only a draft-ready paired Scene Plan and Visual Plan can be edited.", 409, retryable=False)
        script, analysis, style = self.validate_current_lineage(project, {"script_artifact_id": scene_plan["source_script_revision_id"], "script_analysis_artifact_id": scene_plan["source_script_analysis_revision_id"], "style_bible_artifact_id": scene_plan["source_style_bible_revision_id"], "expected_scene_plan_artifact_id": scene_plan["scene_plan_revision_id"], "expected_visual_plan_artifact_id": visual_plan["visual_plan_revision_id"]}, require_scene_plan=True)
        sections = extract_ordered_sections(script, analysis)
        section = next((item for item in sections if item.section_id == section_id), None)
        if section is None:
            raise AppError("SCENE_BREAKDOWN_SECTION_NOT_FOUND", "The requested Script section was not found.", 404, retryable=False, details={"section_id": section_id})
        template = editable_section_from_paired_plans(scene_plan, visual_plan, section_id)
        return script, analysis, style, section, template

    @staticmethod
    def _edit_error_response(errors: list[dict[str, str]], warnings: list[str] | None = None) -> dict[str, Any]:
        return {
            "valid": False,
            "validation_artifact_id": None,
            "errors": errors,
            "warnings": list(warnings or []),
        }

    @staticmethod
    def _carry_from_fragment(previous: dict[str, Any] | None) -> dict[str, Any] | None:
        if not previous or not previous.get("scenes"):
            return None
        last_scene = previous["scenes"][-1]
        last_beat = last_scene["beats"][-1]
        visual = last_beat["visual"]
        visual_state = visual.get("subject_action") or visual.get("visual_concept") or "Previous visual state"
        return {
            "previous_visual_tail": {
                "visual_concept": str(visual["visual_concept"]),
                "selected_character_refs": deepcopy(visual["selected_character_refs"]),
                "selected_location_refs": visual["selected_location_refs"],
                "selected_prop_refs": deepcopy(visual["selected_prop_refs"]),
                "selected_recurring_visual_subject_refs": deepcopy(visual["selected_recurring_visual_subject_refs"]),
                "selected_motif_refs": deepcopy(visual["selected_motif_refs"]),
                "framing": visual["framing"],
                "composition": visual["composition"],
                "visual_state": str(visual_state),
            },
        }

    def _validated_carry_before_section(
        self,
        *,
        script: dict[str, Any],
        analysis: dict[str, Any],
        style: dict[str, Any],
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        section_id: str,
        word_timing_map: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Rebuild the exact current prefix and derive canonical edit carry.

        Section review/edit must observe the same global continuity chain as
        full generation. The paired ScenePlan + VisualPlan is reconstructed into
        reusable combined fragments, each preceding Section is revalidated in
        order, and carry is derived only from the immediately previous validated
        Beat via the existing canonical carry helper.
        """

        source_fragments = {
            item["section_id"]: item
            for item in combined_fragments_from_paired_plans(
                scene_plan,
                visual_plan,
            )
        }
        previous: dict[str, Any] | None = None
        for current in extract_ordered_sections(script, analysis):
            if current.section_id == section_id:
                return self._carry_from_fragment(previous)
            source_fragment = source_fragments.get(current.section_id)
            if not isinstance(source_fragment, dict):
                raise AppError(
                    "SCENE_BREAKDOWN_EDIT_SOURCE_INVALID",
                    "Current paired Scene/Visual Plan is missing a preceding Script section.",
                    409,
                    retryable=False,
                    details={"section_id": current.section_id},
                )
            carry = self._carry_from_fragment(previous)
            section_input = build_section_input(
                section=current,
                script_analysis=analysis,
                style_bible=style,
                carry_over=carry,
                video_pacing_profile=script["video_pacing_profile"],
            )
            previous = validate_reusable_section_fragment(
                fragment=source_fragment,
                section=current,
                section_input=section_input,
                script_analysis=analysis,
                style_bible=style,
                word_timing_map=word_timing_map,
            )
        raise AppError(
            "SCENE_BREAKDOWN_SECTION_NOT_FOUND",
            "The requested Script section was not found.",
            404,
            retryable=False,
            details={"section_id": section_id},
        )

    def validate_section_edit(
        self, project_id: str, section_id: str, request_payload: dict[str, Any]
    ) -> dict[str, Any]:
        request = self.v4.validate("scene_breakdown_section_edit_validate_request_v1.schema.json", deepcopy(request_payload), error_code="SCENE_BREAKDOWN_EDIT_REQUEST_INVALID")
        project = self._project(project_id)
        expected_scene = request["expected_scene_plan_artifact_id"]
        expected_visual = request["expected_visual_plan_artifact_id"]
        self.validate_current_lineage(project, {"script_artifact_id": project.script_artifact_id, "script_analysis_artifact_id": project.script_analysis_artifact_id, "style_bible_artifact_id": project.style_bible_artifact_id, "expected_scene_plan_artifact_id": expected_scene, "expected_visual_plan_artifact_id": expected_visual}, require_scene_plan=True)
        scene_plan = self.loader.load_scene_plan(project, expected_scene, require_current=True)
        visual_plan = self.visual_plan_loader.load_visual_plan(project, expected_visual, require_current=True)
        script, analysis, style, section, template = self._load_edit_context(project=project, scene_plan=scene_plan, visual_plan=visual_plan, section_id=section_id)
        _audio, word_timing = self.approved_voice_timing(project)
        candidate = request["section"]
        issues = fixed_shape_issues(template, candidate)
        if candidate.get("section_id") != section_id:
            issues.append({"path": "$.section_id", "code": "section_id_changed", "message": "The section_id value is immutable."})
        if not issues:
            issues.extend(source_ownership_issues(template, candidate))
        if issues:
            return self.v4.validate("scene_breakdown_section_edit_validation_response_v1.schema.json", self._edit_error_response(issues), error_code="SCENE_BREAKDOWN_EDIT_RESPONSE_INVALID")
        carry = self._validated_carry_before_section(
            script=script,
            analysis=analysis,
            style=style,
            scene_plan=scene_plan,
            visual_plan=visual_plan,
            section_id=section_id,
            word_timing_map=word_timing,
        )
        section_input = build_section_input(
            section=section,
            script_analysis=analysis,
            style_bible=style,
            carry_over=carry,
            video_pacing_profile=script["video_pacing_profile"],
        )
        try:
            fragment = validate_and_normalize_section(raw_output=raw_output_from_editable(candidate), section=section, section_input=section_input, script_analysis=analysis, style_bible=style, word_timing_map=word_timing)
        except SceneBreakdownValidationError as exc:
            errors = deepcopy(exc.report.get("errors") or exc.report.get("semantic_errors") or [])
            warnings = list(exc.report.get("warnings", [])) + list(exc.report.get("timing_warnings", []))
            return self.v4.validate("scene_breakdown_section_edit_validation_response_v1.schema.json", self._edit_error_response(errors, warnings), error_code="SCENE_BREAKDOWN_EDIT_RESPONSE_INVALID")
        validation_id = self.artifacts.preallocate_artifact_id()
        validation = {"artifact_type": "scene_breakdown_section_edit_validation", "schema_version": "1.0", "validation_artifact_id": validation_id, "project_id": project_id, "source_scene_plan_revision_id": expected_scene, "source_visual_plan_revision_id": expected_visual, "section_id": section_id, "editable_payload_sha256": "sha256:" + canonical_json_hash(candidate), "editable_section": deepcopy(candidate), "normalized_fragment": fragment, "created_at": utc_now_iso()}
        self.v4.validate("scene_breakdown_section_edit_validation_v1.schema.json", validation, error_code="SCENE_BREAKDOWN_EDIT_VALIDATION_INVALID")
        staged: StagedArtifact | None = None
        try:
            staged = self.artifacts.write_artifact(logical_id=f"project/{project_id}/scene_breakdown_v4/review_validations/{validation_id}", artifact_type=ArtifactType.JSON, filename="scene_breakdown_section_edit_validation.json", content=_json_bytes(validation), artifact_id=validation_id, source_job_id=None)
            self.session.commit()
        except Exception:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged, logger=logger)
            raise
        report = fragment["backend_qc"]
        response = {"valid": True, "validation_artifact_id": validation_id, "errors": [], "warnings": list(report.get("warnings", [])) + list(report.get("timing_warnings", []))}
        return self.v4.validate("scene_breakdown_section_edit_validation_response_v1.schema.json", response, error_code="SCENE_BREAKDOWN_EDIT_RESPONSE_INVALID")

    def save_section_edit(
        self, project_id: str, section_id: str, request_payload: dict[str, Any]
    ) -> dict[str, Any]:
        request = self.v4.validate("scene_breakdown_section_edit_save_request_v1.schema.json", deepcopy(request_payload), error_code="SCENE_BREAKDOWN_EDIT_REQUEST_INVALID")
        project = self._project(project_id)
        expected_scene = request["expected_scene_plan_artifact_id"]
        expected_visual = request["expected_visual_plan_artifact_id"]
        scene_plan = self.loader.load_scene_plan(project, expected_scene, require_current=True)
        visual_plan = self.visual_plan_loader.load_visual_plan(project, expected_visual, require_current=True)
        script, analysis, style, _selected_section, template = self._load_edit_context(project=project, scene_plan=scene_plan, visual_plan=visual_plan, section_id=section_id)
        _audio, word_timing = self.approved_voice_timing(project)
        candidate = request["section"]
        issues = fixed_shape_issues(template, candidate)
        if candidate.get("section_id") != section_id:
            issues.append({"path": "$.section_id", "code": "section_id_changed", "message": "The section_id value is immutable."})
        if not issues:
            issues.extend(source_ownership_issues(template, candidate))
        if issues:
            if any(item.get("code") == "edit_source_ownership_changed" for item in issues):
                raise AppError(
                    "SCENE_BREAKDOWN_EDIT_RESEGMENTATION_FORBIDDEN",
                    "Beat/Scene source ownership is immutable in review/edit; rebuild or repartition Step 1 to change segmentation.",
                    422,
                    retryable=False,
                    details={"errors": issues},
                )
            raise AppError("SCENE_BREAKDOWN_EDIT_STRUCTURE_CHANGED", "The validated JSON structure changed before save.", 422, retryable=False, details={"errors": issues})
        validation = self.loader.load_section_edit_validation(project, request["validation_artifact_id"])
        payload_hash = "sha256:" + canonical_json_hash(candidate)
        if validation.get("source_scene_plan_revision_id") != expected_scene or validation.get("source_visual_plan_revision_id") != expected_visual or validation.get("section_id") != section_id or validation.get("editable_payload_sha256") != payload_hash or validation.get("editable_section") != candidate:
            raise AppError("SCENE_BREAKDOWN_EDIT_VALIDATION_STALE", "The section JSON must be validated again before save.", 409, retryable=False)
        source_fragments = {item["section_id"]: item for item in combined_fragments_from_paired_plans(scene_plan, visual_plan)}
        ordered_sections = extract_ordered_sections(script, analysis)
        rebuilt: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for section in ordered_sections:
            carry = self._carry_from_fragment(previous)
            section_input = build_section_input(section=section, script_analysis=analysis, style_bible=style, carry_over=carry, video_pacing_profile=script["video_pacing_profile"])
            if section.section_id == section_id:
                fragment = deepcopy(validation["normalized_fragment"])
            else:
                fragment = validate_reusable_section_fragment(fragment=source_fragments[section.section_id], section=section, section_input=section_input, script_analysis=analysis, style_bible=style, word_timing_map=word_timing)
            rebuilt.append(fragment)
            previous = fragment
        new_scene_id = self.artifacts.preallocate_artifact_id()
        new_visual_id = self.artifacts.preallocate_artifact_id()
        edited_scene = assemble_scene_plan(project_id=project_id, revision_id=new_scene_id, parent_revision_id=expected_scene, revision_operation="section_edit", source_script_revision_id=scene_plan["source_script_revision_id"], source_script_analysis_revision_id=scene_plan["source_script_analysis_revision_id"], source_style_bible_revision_id=scene_plan["source_style_bible_revision_id"], source_run_artifact_id=scene_plan["source_run_artifact_id"], ordered_sections=rebuilt, status="draft_ready")
        approval_id, bindings = self.visual_reference_loader.approved_bindings(project)
        edited_visual = assemble_visual_plan_from_scene_breakdown(scene_plan=edited_scene, text_approved_style_bible=style, ordered_sections=rebuilt, visual_plan_revision_id=new_visual_id, parent_visual_plan_revision_id=expected_visual, source_run_artifact_id=scene_plan["source_run_artifact_id"], revision_operation="section_edit", status="draft_ready", canonical_reference_bindings=bindings, source_visual_reference_approval_set_artifact_id=approval_id)
        guard = SceneVisualPlanCommitGuard(narration_audio_artifact_id=str(project.narration_audio_artifact_id),
narration_word_timing_map_artifact_id=str(project.narration_word_timing_map_artifact_id),
script_artifact_id=scene_plan["source_script_revision_id"], script_analysis_artifact_id=scene_plan["source_script_analysis_revision_id"], style_bible_artifact_id=scene_plan["source_style_bible_revision_id"], visual_reference_approval_set_artifact_id=approval_id, previous_scene_plan_artifact_id=expected_scene, previous_visual_plan_artifact_id=expected_visual)
        self.publish_scene_visual_plan(project_id=project_id, scene_plan=edited_scene, visual_plan=edited_visual, guard=guard, source_job_id=None)
        response = {"scene_plan_artifact_id": new_scene_id, "scene_plan": edited_scene, "visual_plan_artifact_id": new_visual_id, "visual_plan": edited_visual}
        return self.v4.validate("scene_breakdown_revision_response_v1.schema.json", response, error_code="SCENE_BREAKDOWN_RESPONSE_INVALID")

    def approve(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        request = self.v4.validate(
            "scene_plan_approve_request_v1.schema.json",
            deepcopy(request_payload),
            error_code="SCENE_PLAN_APPROVE_REQUEST_INVALID",
        )
        project = self._project(project_id)
        expected_scene = request["expected_scene_plan_artifact_id"]
        expected_visual = request["expected_visual_plan_artifact_id"]
        scene_plan = self.loader.load_scene_plan(project, expected_scene, require_current=True)
        visual_plan = self.visual_plan_loader.load_visual_plan(project, expected_visual, require_current=True)
        if scene_plan["status"] != "draft_ready" or visual_plan["status"] != "draft_ready":
            raise AppError(
                "SCENE_VISUAL_PLAN_STATUS_INVALID",
                "Only a draft-ready paired Scene Plan and Visual Plan can be approved.",
                409,
                retryable=False,
            )
        _script, analysis, style = self.validate_current_lineage(
            project,
            {
                "script_artifact_id": scene_plan["source_script_revision_id"],
                "script_analysis_artifact_id": scene_plan["source_script_analysis_revision_id"],
                "style_bible_artifact_id": scene_plan["source_style_bible_revision_id"],
                "expected_scene_plan_artifact_id": expected_scene,
                "expected_visual_plan_artifact_id": expected_visual,
            },
            require_scene_plan=True,
        )
        new_scene_id = self.artifacts.preallocate_artifact_id()
        new_visual_id = self.artifacts.preallocate_artifact_id()
        new_prompt_bundle_id = self.artifacts.preallocate_artifact_id()
        new_beat_timing_map_id = self.artifacts.preallocate_artifact_id()
        approved_scene = approve_scene_plan(scene_plan, new_scene_id)
        approved_visual = approve_visual_plan_for_scene_plan(
            visual_plan,
            new_revision_id=new_visual_id,
            approved_scene_plan_revision_id=new_scene_id,
        )
        _voice_audio, word_timing_map = self.approved_voice_timing(project)
        beat_timing_map = compile_beat_timing_map(
            project_id=project_id,
            script_artifact_id=str(project.script_artifact_id),
            scene_plan_artifact_id=new_scene_id,
            narration_audio_artifact_id=str(project.narration_audio_artifact_id),
            narration_word_timing_map_artifact_id=str(project.narration_word_timing_map_artifact_id),
            scene_plan=approved_scene,
            word_timing_map=word_timing_map,
        )
        self.v4.validate("beat_timing_map_v1.schema.json", beat_timing_map, error_code="BEAT_TIMING_MAP_INVALID")
        prompt_bundle = PromptComposerService(self.session).build_automatic_approved_bundle(
            approved_visual_plan=approved_visual,
            text_approved_style_bible=style,
            canonical_script_analysis=analysis,
            options={
                "schema_version": "1.0",
                "prompt_language": "en",
                "aspect_ratio": request["aspect_ratio"],
                "target_asset_role": "beat_visual",
                "image_count": 1,
            },
            revision_id=new_prompt_bundle_id,
        )
        approval_id = project.style_bible_visual_reference_approval_set_artifact_id
        guard = SceneVisualPromptBundleCommitGuard(
            script_artifact_id=scene_plan["source_script_revision_id"],
            script_analysis_artifact_id=scene_plan["source_script_analysis_revision_id"],
            style_bible_artifact_id=scene_plan["source_style_bible_revision_id"],
            visual_reference_approval_set_artifact_id=approval_id,
            narration_audio_artifact_id=str(project.narration_audio_artifact_id),
            narration_word_timing_map_artifact_id=str(project.narration_word_timing_map_artifact_id),
            previous_scene_plan_artifact_id=expected_scene,
            previous_visual_plan_artifact_id=expected_visual,
            previous_prompt_bundle_artifact_id=project.prompt_bundle_artifact_id,
            previous_beat_timing_map_artifact_id=project.beat_timing_map_artifact_id,
        )
        self.publish_approved_scene_visual_prompt_bundle(
            project_id=project_id,
            scene_plan=approved_scene,
            visual_plan=approved_visual,
            prompt_bundle=prompt_bundle,
            beat_timing_map=beat_timing_map,
            beat_timing_map_artifact_id=new_beat_timing_map_id,
            guard=guard,
        )
        response = {
            "scene_plan_artifact_id": new_scene_id,
            "scene_plan": approved_scene,
            "visual_plan_artifact_id": new_visual_id,
            "visual_plan": approved_visual,
        }
        return self.v4.validate(
            "scene_breakdown_revision_response_v1.schema.json",
            response,
            error_code="SCENE_BREAKDOWN_RESPONSE_INVALID",
        )
