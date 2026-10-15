from __future__ import annotations

from copy import deepcopy
import json
import logging
import re
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.prompt_composer_schemas_v4 import PromptBundleModel
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactType
from app.db.models import Project
from app.modules.platform.artifact_loader import ProjectArtifactLoader
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.prompt_composer_loader import PromptBundleArtifactLoaderV4
from app.modules.v4.style_bible_consumer_loader import StyleBibleConsumerLoaderV4
from app.modules.v4.visual_plan_loader import VisualPlanArtifactLoaderV4
from app.modules.v4.prompt_composer import compile_prompt_bundle, compute_prompt_hash
from app.modules.v4.prompt_composer_input_builder import PromptComposerError, build_prompt_composer_input
from app.modules.v4.prompt_bundle_validator import validate_prompt_bundle_revision
from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService
from app.modules.v4.script_analysis_loader import ScriptAnalysisArtifactLoader
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.repositories import ArtifactRepository, ProjectRepository
from app.repositories.core import PromptBundleScriptAnalysisCommitGuard, SceneAssetBeatOperationLeaseRepository
from app.services.artifacts import ArtifactService, StagedArtifact, cleanup_staged_artifact_safely, rollback_safely

logger = logging.getLogger(__name__)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _critical_qc_passed(payload: dict[str, Any]) -> bool:
    qc = payload.get("backend_qc")
    return isinstance(qc, dict) and all(value == "passed" for key, value in qc.items() if key.endswith("_status"))


class PromptComposerService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifact_repository = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifact_repository)
        self.beat_leases = SceneAssetBeatOperationLeaseRepository(session)
        self.prompt_revisions = SceneAssetPromptRevisionService(session)
        self.validator = V4ContractValidator()
        self.bundle_loader = PromptBundleArtifactLoaderV4(self.artifacts)
        self.style_loader = StyleBibleConsumerLoaderV4(self.artifacts)
        self.visual_loader = VisualPlanArtifactLoaderV4(self.artifacts)
        self.script_loader = ProjectArtifactLoader(self.artifacts, PlatformContractValidator())
        self.analysis_loader = ScriptAnalysisArtifactLoader(
            self.artifacts,
            self.validator,
            ScriptAnalysisSemanticValidator(),
        )

    def _project(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        return project

    def get_current_prompt_bundle(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        if project.prompt_bundle_artifact_id is None:
            response = {"project_id": project_id, "generated": False, "artifact_id": None, "artifact": None}
        else:
            artifact = self.bundle_loader.load(project, project.prompt_bundle_artifact_id, require_current=True)
            response = {"project_id": project_id, "generated": True, "artifact_id": project.prompt_bundle_artifact_id, "artifact": artifact}
        return self.validator.validate("prompt_bundle_read_response_v1.schema.json", response, error_code="PROMPT_BUNDLE_RESPONSE_INVALID")

    def compile_initial(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        request = self.validator.validate("prompt_bundle_compile_request_v1.schema.json", deepcopy(request_payload), error_code="PROMPT_COMPOSER_REQUEST_INVALID")
        project = self._project(project_id)
        self._validate_prompt_composer_prerequisites(project)
        if project.prompt_bundle_artifact_id is not None:
            raise AppError("PROMPT_BUNDLE_ALREADY_EXISTS", "Project already has a current Prompt Bundle.", 409, retryable=False)
        if request.get("expected_prompt_bundle_artifact_id") is not None:
            raise AppError("PROMPT_BUNDLE_ALREADY_EXISTS", "Initial Prompt Bundle compilation requires no current Prompt Bundle.", 409, retryable=False)
        project, sources = self._validate_sources(project_id, request, require_prompt_bundle=False)
        new_id = self.artifacts.preallocate_artifact_id()
        bundle = self._compile_canonical(sources=sources, options=request["options"], revision_id=new_id)
        return self._publish_response(project, bundle, previous_prompt_bundle_artifact_id=None)

    def recompile_full(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        request = self.validator.validate("prompt_bundle_recompile_request_v1.schema.json", deepcopy(request_payload), error_code="PROMPT_COMPOSER_REQUEST_INVALID")
        preliminary_project, _ = self._validate_sources(project_id, request, require_prompt_bundle=True)
        preliminary_parent = self._load_parent(preliminary_project, request)
        beat_ids = [str(item["beat_id"]) for item in preliminary_parent.get("prompt_items", [])]
        lease_owner_id = generate_id("scene_asset_lease")
        try:
            self.beat_leases.acquire_many(
                project_id=project_id,
                beat_ids=beat_ids,
                owner_kind="prompt_recompile_full",
                owner_id=lease_owner_id,
                created_at=utc_now_iso(),
            )
        except IntegrityError as exc:
            rollback_safely(self.session, logger=logger)
            raise AppError(
                "SCENE_ASSET_BEAT_OPERATION_ACTIVE",
                "A Scene Asset beat already has an active operation.",
                409,
                retryable=False,
                details={"project_id": project_id, "beat_ids": beat_ids},
            ) from exc
        self.session.expire_all()
        project, sources = self._validate_sources(project_id, request, require_prompt_bundle=True)
        parent = self._load_parent(project, request)
        if parent["status"] not in {"draft_ready", "approved"}:
            raise AppError("PROMPT_BUNDLE_STATUS_INVALID", "Current Prompt Bundle status cannot be recompiled.", 409, retryable=False)
        new_id = self.artifacts.preallocate_artifact_id()
        bundle = self._compile_canonical(
            sources=sources,
            options=request["options"],
            revision_id=new_id,
            parent_revision_id=parent["prompt_bundle_revision_id"],
        )
        return self._publish_response(
            project,
            bundle,
            previous_prompt_bundle_artifact_id=parent["prompt_bundle_revision_id"],
            before_commit=lambda owner_id=lease_owner_id: self.beat_leases.release_owner(owner_id),
        )

    def recompile_beat(self, project_id: str, beat_id: str, request_payload: dict[str, Any], *, before_commit: Callable[[], None] | None = None) -> dict[str, Any]:
        request = self.validator.validate("prompt_bundle_beat_recompile_request_v1.schema.json", deepcopy(request_payload), error_code="PROMPT_COMPOSER_REQUEST_INVALID")
        project, sources = self._validate_sources(project_id, request, require_prompt_bundle=True, allow_prompt_bundle_rebase=True)
        parent = self._load_rebase_parent(project, request, beat_id)
        parent_item = self._single_prompt_item(parent, beat_id)
        new_id = self.artifacts.preallocate_artifact_id()
        canonical = self._compile_canonical(
            sources=sources,
            options=parent["options"],
            revision_id=new_id,
            parent_revision_id=parent["prompt_bundle_revision_id"],
            revision_operation="beat_recompilation",
            status="approved",
            operation_target_beat_id=beat_id,
        )
        target = self._single_prompt_item(canonical, beat_id)
        bundle = self._base_child(parent, new_id, "beat_recompilation", beat_id)
        for item in bundle["prompt_items"]:
            if item["beat_id"] == beat_id:
                item["prompt_item_id"] = parent_item["prompt_item_id"]
                item["prompt_origin"] = "compiled"
                item["image_prompt"] = target["image_prompt"]
                item["negative_prompt"] = target["negative_prompt"]
                item["prompt_hash"] = target["prompt_hash"]
                break
        return self._publish_response(
            project,
            self._validate_revision(
                operation="beat_recompilation",
                candidate=bundle,
                parent=parent,
                canonical_candidate=canonical,
                target_beat_id=beat_id,
            ),
            previous_prompt_bundle_artifact_id=parent["prompt_bundle_revision_id"],
            before_commit=before_commit,
        )

    def edit_beat(self, project_id: str, beat_id: str, request_payload: dict[str, Any], *, before_commit: Callable[[], None] | None = None) -> dict[str, Any]:
        request = self.validator.validate("prompt_bundle_manual_edit_request_v1.schema.json", deepcopy(request_payload), error_code="PROMPT_COMPOSER_REQUEST_INVALID")
        self._validate_manual_prompt_text(request["image_prompt"])
        self._validate_manual_prompt_text(request["negative_prompt"])
        project, _ = self._validate_sources(project_id, request, require_prompt_bundle=True, allow_prompt_bundle_rebase=True)
        parent = self._load_rebase_parent(project, request, beat_id)
        self._single_prompt_item(parent, beat_id)
        new_id = self.artifacts.preallocate_artifact_id()
        bundle = self._base_child(parent, new_id, "manual_edit", beat_id)
        for item in bundle["prompt_items"]:
            if item["beat_id"] == beat_id:
                item["prompt_origin"] = "manual_edit"
                item["image_prompt"] = request["image_prompt"]
                item["negative_prompt"] = request["negative_prompt"]
                item["prompt_hash"] = compute_prompt_hash(
                    image_prompt=item["image_prompt"],
                    negative_prompt=item["negative_prompt"],
                    generation_options=item["generation_options"],
                    reference_bindings=item["reference_bindings"],
                )
                break
        return self._publish_response(
            project,
            self._validate_revision(
                operation="manual_edit",
                candidate=bundle,
                parent=parent,
                target_beat_id=beat_id,
            ),
            previous_prompt_bundle_artifact_id=parent["prompt_bundle_revision_id"],
            before_commit=before_commit,
        )

    def approve(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        request = self.validator.validate("prompt_bundle_approve_request_v1.schema.json", deepcopy(request_payload), error_code="PROMPT_COMPOSER_REQUEST_INVALID")
        project, _ = self._validate_sources(project_id, request, require_prompt_bundle=True)
        parent = self._load_parent(project, request)
        if parent["status"] == "approved":
            raise AppError("PROMPT_BUNDLE_ALREADY_APPROVED", "The current Prompt Bundle is already approved.", 409, retryable=False)
        if parent["status"] != "draft_ready":
            raise AppError("PROMPT_BUNDLE_STATUS_INVALID", "Only a draft-ready Prompt Bundle may be approved.", 409, retryable=False)
        if not _critical_qc_passed(parent):
            raise AppError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle QA must pass before approval.", 409, retryable=False)
        new_id = self.artifacts.preallocate_artifact_id()
        bundle = self._base_child(parent, new_id, "approval", None)
        bundle["status"] = "approved"
        return self._publish_response(
            project,
            self._validate_revision(operation="approval", candidate=bundle, parent=parent),
            previous_prompt_bundle_artifact_id=parent["prompt_bundle_revision_id"],
        )

    def _validate_sources(
        self,
        project_id: str,
        request: dict[str, Any],
        *,
        require_prompt_bundle: bool,
        allow_prompt_bundle_rebase: bool = False,
    ) -> tuple[Project, dict[str, dict[str, Any]]]:
        project = self._project(project_id)
        self._validate_prompt_composer_prerequisites(project)
        expected = {
            "style_bible_artifact_id": project.style_bible_artifact_id,
            "visual_plan_artifact_id": project.visual_plan_artifact_id,
            "expected_prompt_bundle_artifact_id": project.prompt_bundle_artifact_id,
        }
        if require_prompt_bundle and project.prompt_bundle_artifact_id is None:
            raise AppError("PROMPT_BUNDLE_NOT_FOUND", "A current Prompt Bundle is required.", 404, retryable=False)
        for field, current in expected.items():
            if field == "expected_prompt_bundle_artifact_id" and allow_prompt_bundle_rebase:
                if request.get(field) is None:
                    raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "Prompt Bundle request lineage is no longer current.", 409, retryable=False, details={"field": field})
                continue
            if request.get(field) != current:
                code = "PROMPT_BUNDLE_REVISION_CONFLICT" if field == "expected_prompt_bundle_artifact_id" else "PROMPT_BUNDLE_UPSTREAM_STALE"
                raise AppError(code, "Prompt Bundle request lineage is no longer current.", 409, retryable=False, details={"field": field})
        style = self.style_loader.load_text_approved(project, str(project.style_bible_artifact_id))
        visual = self.visual_loader.load_approved_visual_plan_for_consumer(project, str(project.visual_plan_artifact_id))
        script = self.script_loader.load_script(
            project,
            str(project.script_artifact_id),
            require_current=True,
        )
        analysis = self.analysis_loader.load(
            project=project,
            artifact_id=str(project.script_analysis_artifact_id),
            script_artifact=script,
            expected_script_revision_id=str(project.script_artifact_id),
            require_current=True,
        )
        if not _critical_qc_passed(visual):
            raise AppError("PROMPT_COMPOSER_VISUAL_PLAN_QA_FAILED", "Visual Plan QA must pass before Prompt Composer.", 409, retryable=False)
        if (
            visual.get("source_script_revision_id") != project.script_artifact_id
            or visual.get("source_script_analysis_revision_id") != project.script_analysis_artifact_id
            or visual.get("source_style_bible_revision_id") != project.style_bible_artifact_id
            or visual.get("source_scene_plan_revision_id") != project.scene_plan_artifact_id
            or visual.get("source_visual_reference_approval_set_artifact_id")
            != project.style_bible_visual_reference_approval_set_artifact_id
        ):
            raise AppError("PROMPT_COMPOSER_SOURCE_LINEAGE_MISMATCH", "Prompt Composer source lineage is inconsistent.", 409, retryable=False)
        return project, {"style": style, "visual": visual, "analysis": analysis}

    @staticmethod
    def _validate_prompt_composer_prerequisites(project: Project) -> None:
        for field in (
            "script_artifact_id", "script_analysis_artifact_id", "style_bible_artifact_id",
            "scene_plan_artifact_id", "visual_plan_artifact_id",
        ):
            if getattr(project, field) is None:
                raise AppError(
                    "PROMPT_COMPOSER_PREREQUISITES_REQUIRED",
                    "Current Script, Script Analysis, Style Bible, Scene Plan, and Visual Plan lineage are required.",
                    409, retryable=False, details={"field": field},
                )

    def build_automatic_approved_bundle(
        self,
        *,
        approved_visual_plan: dict[str, Any],
        text_approved_style_bible: dict[str, Any],
        canonical_script_analysis: dict[str, Any],
        options: dict[str, Any],
        revision_id: str,
    ) -> dict[str, Any]:
        """Compile the internal approved Prompt Bundle for Scene Breakdown approval.

        This method is deliberately persistence-free so the Scene Breakdown owner
        can publish Scene Plan, Visual Plan, and Prompt Bundle in one transaction.
        """
        return self._compile_canonical(
            sources={"visual": approved_visual_plan, "style": text_approved_style_bible, "analysis": canonical_script_analysis},
            options=options,
            revision_id=revision_id,
            revision_operation="automatic_compilation",
            status="approved",
            validation_operation="automatic_compilation",
        )

    def _compile_canonical(
        self,
        *,
        sources: dict[str, dict[str, Any]],
        options: dict[str, Any],
        revision_id: str,
        parent_revision_id: str | None = None,
        revision_operation: str = "full_compilation",
        status: str = "draft_ready",
        operation_target_beat_id: str | None = None,
        validation_operation: str = "full_compilation",
    ) -> dict[str, Any]:
        try:
            composer_input = build_prompt_composer_input(
                approved_visual_plan=sources["visual"],
                text_approved_style_bible=sources["style"],
                canonical_script_analysis=sources["analysis"],
                options=options,
                prompt_bundle_revision_id=revision_id,
            )
            bundle = compile_prompt_bundle(
                prompt_composer_input=composer_input,
                parent_prompt_bundle_revision_id=parent_revision_id,
                revision_operation=revision_operation,
                status=status,
                operation_target_beat_id=operation_target_beat_id,
            )
            bundle["backend_qc"] = validate_prompt_bundle_revision(
                operation=validation_operation,
                bounded_input=composer_input,
                candidate=bundle,
            )
            return self.validator.validate(
                "prompt_bundle_v1.schema.json",
                bundle,
                error_code="PROMPT_BUNDLE_VALIDATION_FAILED",
            )
        except PromptComposerError as exc:
            raise AppError(exc.code, exc.message, 409, retryable=False, details=exc.details) from exc

    def _load_parent(self, project: Project, request: dict[str, Any]) -> dict[str, Any]:
        parent_id = request.get("expected_prompt_bundle_artifact_id")
        if not parent_id or project.prompt_bundle_artifact_id != parent_id:
            raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "The expected Prompt Bundle is no longer current.", 409, retryable=False)
        parent = self.bundle_loader.load(project, parent_id, require_current=True)
        if parent.get("parent_prompt_bundle_revision_id") == parent.get("prompt_bundle_revision_id"):
            raise AppError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle parent lineage is invalid.", 409, retryable=False)
        return parent

    def _load_rebase_parent(self, project: Project, request: dict[str, Any], beat_id: str) -> dict[str, Any]:
        requested_id = request.get("expected_prompt_bundle_artifact_id")
        current_id = project.prompt_bundle_artifact_id
        if not requested_id or current_id is None:
            raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "The expected Prompt Bundle is no longer current.", 409, retryable=False)
        if requested_id == current_id:
            return self._load_parent(project, request)
        requested = self.bundle_loader.load(project, str(requested_id), require_current=False)
        current = self.bundle_loader.load(project, str(current_id), require_current=True)
        self._assert_prompt_bundle_rebase_allowed(project=project, requested=requested, current=current, target_beat_id=beat_id)
        return current

    def _assert_prompt_bundle_rebase_allowed(self, *, project: Project, requested: dict[str, Any], current: dict[str, Any], target_beat_id: str) -> None:
        requested_revision = requested.get("prompt_bundle_revision_id")
        current_revision = current.get("prompt_bundle_revision_id")
        if not requested_revision or not current_revision:
            raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "Prompt Bundle lineage is invalid.", 409, retryable=False)
        self._assert_prompt_bundle_structure_compatible(requested, current)
        self._assert_prompt_item_unchanged(requested, current, target_beat_id)
        cursor = current
        seen: set[str] = set()
        while cursor.get("prompt_bundle_revision_id") != requested_revision:
            cursor_revision = str(cursor.get("prompt_bundle_revision_id") or "")
            if not cursor_revision or cursor_revision in seen:
                raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "Prompt Bundle lineage is invalid.", 409, retryable=False)
            seen.add(cursor_revision)
            if cursor.get("revision_operation") not in {"manual_edit", "beat_recompilation"}:
                raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "Prompt Bundle changed in a way that cannot be safely rebased.", 409, retryable=False)
            if cursor.get("operation_target_beat_id") == target_beat_id:
                raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "The target beat prompt changed before publication.", 409, retryable=False)
            parent_id = cursor.get("parent_prompt_bundle_revision_id")
            if not parent_id:
                raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "Prompt Bundle lineage is invalid.", 409, retryable=False)
            cursor = self.bundle_loader.load(project, str(parent_id), require_current=False)

    @staticmethod
    def _assert_prompt_bundle_structure_compatible(requested: dict[str, Any], current: dict[str, Any]) -> None:
        source_fields = (
            "source_script_revision_id",
            "source_script_analysis_revision_id",
            "source_style_bible_revision_id",
            "source_scene_plan_revision_id",
            "source_visual_plan_revision_id",
            "source_visual_reference_approval_set_artifact_id",
        )
        if any(requested.get(field) != current.get(field) for field in source_fields):
            raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "Prompt Bundle upstream lineage changed before publication.", 409, retryable=False)
        if requested.get("options") != current.get("options"):
            raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "Prompt Bundle options changed before publication.", 409, retryable=False)
        requested_keys = [(item.get("beat_id"), item.get("prompt_item_id")) for item in requested.get("prompt_items", [])]
        current_keys = [(item.get("beat_id"), item.get("prompt_item_id")) for item in current.get("prompt_items", [])]
        if requested_keys != current_keys:
            raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "Prompt Bundle structure changed before publication.", 409, retryable=False)

    def _assert_prompt_item_unchanged(self, requested: dict[str, Any], current: dict[str, Any], beat_id: str) -> None:
        requested_item = self._single_prompt_item(requested, beat_id)
        current_item = self._single_prompt_item(current, beat_id)
        fields = ("image_prompt", "negative_prompt", "prompt_hash", "generation_options", "reference_bindings")
        if any(requested_item.get(field) != current_item.get(field) for field in fields):
            raise AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "The target beat prompt changed before publication.", 409, retryable=False)

    @staticmethod
    def _single_prompt_item(bundle: dict[str, Any], beat_id: str) -> dict[str, Any]:
        matches = [item for item in bundle.get("prompt_items", []) if item.get("beat_id") == beat_id]
        if not matches:
            raise AppError("PROMPT_BUNDLE_BEAT_NOT_FOUND", "Prompt Bundle beat was not found.", 404, retryable=False, details={"beat_id": beat_id})
        if len(matches) != 1:
            raise AppError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle beat identity is not unique.", 409, retryable=False)
        return matches[0]

    @staticmethod
    def _base_child(parent: dict[str, Any], new_id: str, operation: str, beat_id: str | None) -> dict[str, Any]:
        child = deepcopy(parent)
        child["prompt_bundle_revision_id"] = new_id
        child["parent_prompt_bundle_revision_id"] = parent["prompt_bundle_revision_id"]
        child["revision_operation"] = operation
        child["operation_target_beat_id"] = beat_id
        child["status"] = "approved" if operation in {"manual_edit", "beat_recompilation"} else "draft_ready"
        return child

    @staticmethod
    def _validate_manual_prompt_text(value: str) -> None:
        if not value.strip():
            raise AppError("PROMPT_BUNDLE_MANUAL_EDIT_INVALID", "Prompt text must not be empty.", 422, retryable=False)
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
            raise AppError("PROMPT_BUNDLE_MANUAL_EDIT_INVALID", "Prompt text contains disallowed control characters.", 422, retryable=False)
        stripped = value.strip()
        if "```" in value or (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
            raise AppError("PROMPT_BUNDLE_MANUAL_EDIT_INVALID", "Prompt text cannot be code-fenced JSON or structured provider syntax.", 422, retryable=False)
        forbidden = ("--ar ", "--seed", "cfg_scale", "sampler=", "steps=", "model=", "provider=")
        if any(token in value.lower() for token in forbidden):
            raise AppError("PROMPT_BUNDLE_MANUAL_EDIT_INVALID", "Prompt text cannot include provider-specific option syntax.", 422, retryable=False)

    @staticmethod
    def _validate_model(bundle: dict[str, Any]) -> dict[str, Any]:
        try:
            return PromptBundleModel.model_validate(bundle).model_dump(mode="json")
        except ValidationError as exc:
            raise AppError("PROMPT_BUNDLE_VALIDATION_FAILED", "Prompt Bundle revision failed validation.", 409, retryable=False, details={"errors": exc.errors(include_url=False)}) from exc

    def _validate_revision(
        self,
        *,
        operation: str,
        candidate: dict[str, Any],
        parent: dict[str, Any] | None = None,
        canonical_candidate: dict[str, Any] | None = None,
        target_beat_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            candidate["backend_qc"] = validate_prompt_bundle_revision(
                operation=operation,
                bounded_input=None,
                candidate=candidate,
                parent=parent,
                canonical_candidate=canonical_candidate,
                target_beat_id=target_beat_id,
            )
        except PromptComposerError as exc:
            raise AppError(exc.code, exc.message, 409, retryable=False, details=exc.details) from exc
        return self._validate_model(candidate)

    def _publish_response(self, project: Project, bundle: dict[str, Any], *, previous_prompt_bundle_artifact_id: str | None, before_commit: Callable[[], None] | None = None) -> dict[str, Any]:
        artifact_id = bundle["prompt_bundle_revision_id"]
        guard = PromptBundleScriptAnalysisCommitGuard(
            script_artifact_id=str(project.script_artifact_id),
            script_analysis_artifact_id=str(project.script_analysis_artifact_id),
            style_bible_artifact_id=str(project.style_bible_artifact_id),
            scene_plan_artifact_id=str(project.scene_plan_artifact_id),
            visual_plan_artifact_id=str(project.visual_plan_artifact_id),
            style_bible_visual_reference_approval_set_artifact_id=project.style_bible_visual_reference_approval_set_artifact_id,
            previous_prompt_bundle_artifact_id=previous_prompt_bundle_artifact_id,
        )
        staged: StagedArtifact | None = None
        committed = False
        try:
            self.validator.validate("prompt_bundle_v1.schema.json", bundle, error_code="PROMPT_BUNDLE_VALIDATION_FAILED")
            staged = self.artifacts.write_artifact(
                logical_id=f"project/{project.project_id}/prompt_bundle",
                artifact_type=ArtifactType.JSON,
                filename="prompt_bundle.json",
                content=_json_bytes(bundle),
                artifact_id=artifact_id,
                provider_source=None,
            )
            won = self.projects.compare_and_set_prompt_bundle_v4(
                project_id=project.project_id,
                new_prompt_bundle_artifact_id=artifact_id,
                guard=guard,
                updated_at=utc_now_iso(),
                preserve_scene_asset_manifest=bundle.get("revision_operation") in {"manual_edit", "beat_recompilation"},
            )
            if not won:
                raise self._classify_publication_conflict(project.project_id, guard)
            self.session.expire(project, ["prompt_bundle_artifact_id", "updated_at"])
            response = {"artifact_id": artifact_id, "artifact": bundle}
            validated_response = self.validator.validate("prompt_bundle_revision_response_v1.schema.json", response, error_code="PROMPT_BUNDLE_RESPONSE_INVALID")
            if before_commit is not None:
                before_commit()
            self.prompt_revisions.publish_approved_bundle(project.project_id, bundle)
            self.session.commit()
            committed = True
            return validated_response
        except AppError:
            if not committed:
                rollback_safely(self.session, logger=logger)
                cleanup_staged_artifact_safely(staged, logger=logger)
            raise
        except Exception as exc:
            if not committed:
                rollback_safely(self.session, logger=logger)
                cleanup_staged_artifact_safely(staged, logger=logger)
            raise AppError("PROMPT_BUNDLE_PERSISTENCE_FAILED", "Prompt Bundle could not be persisted.", 500, retryable=False) from exc

    def _classify_publication_conflict(self, project_id: str, guard: PromptBundleScriptAnalysisCommitGuard) -> AppError:
        self.session.expire_all()
        current = self.projects.get(project_id)
        if current is None:
            return AppError("PROJECT_NOT_FOUND", "Project was not found.", 404, retryable=False)
        if (
            current.script_artifact_id != guard.script_artifact_id
            or current.script_analysis_artifact_id != guard.script_analysis_artifact_id
            or current.style_bible_artifact_id != guard.style_bible_artifact_id
            or current.scene_plan_artifact_id != guard.scene_plan_artifact_id
            or current.visual_plan_artifact_id != guard.visual_plan_artifact_id
            or current.style_bible_visual_reference_approval_set_artifact_id
            != guard.style_bible_visual_reference_approval_set_artifact_id
        ):
            return AppError("PROMPT_BUNDLE_UPSTREAM_STALE", "Prompt Bundle upstream lineage changed before publication.", 409, retryable=False)
        return AppError("PROMPT_BUNDLE_REVISION_CONFLICT", "The current Prompt Bundle changed before publication.", 409, retryable=False)
