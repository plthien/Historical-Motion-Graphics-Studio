from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Artifact, Project
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.style_bible_consumer_loader import StyleBibleConsumerLoaderV4
from app.modules.v4.visual_reference_prompt_compiler import build_visual_reference_inventory
from app.repositories import ArtifactRepository, ProjectRepository
from app.repositories.core import StyleBibleVisualReferenceSetCommitGuard
from app.services.artifacts import (
    ArtifactService,
    StagedArtifact,
    cleanup_staged_artifact_safely,
    rollback_safely,
)


logger = logging.getLogger(__name__)
ACTIVE_OPERATION_STATUSES = ("queued", "running", "finalizing")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


class VisualReferencePublicationV2Service:
    """Publish durable Visual Reference working state as immutable V4 snapshots."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifact_rows = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifact_rows)
        self.style_loader = StyleBibleConsumerLoaderV4(self.artifacts)
        self.validator = V4ContractValidator()

    def publish(
        self,
        project_id: str,
        *,
        expected_visual_reference_set_artifact_id: str | None,
        expected_approval_set_artifact_id: str | None,
    ) -> dict[str, Any]:
        staged: list[StagedArtifact] = []
        self.session.execute(text("BEGIN IMMEDIATE"))
        try:
            project = self._project_required(project_id)
            self._assert_expected_pointers(
                project,
                expected_visual_reference_set_artifact_id=expected_visual_reference_set_artifact_id,
                expected_approval_set_artifact_id=expected_approval_set_artifact_id,
            )
            set_logical_id = f"project/{project.project_id}/visual_references"
            approval_logical_id = (
                f"project/{project.project_id}/visual_reference_approvals"
            )
            previous_set_current = self.artifact_rows.get_current_by_logical_id(
                set_logical_id
            )
            previous_approval_current = self.artifact_rows.get_current_by_logical_id(
                approval_logical_id
            )
            expected_set_artifact_current_id = (
                previous_set_current.artifact_id
                if previous_set_current is not None
                else None
            )
            expected_approval_artifact_current_id = (
                previous_approval_current.artifact_id
                if previous_approval_current is not None
                else None
            )
            inventory = self._current_inventory(project)
            approved = self._approved_snapshot(project, inventory)
            now = utc_now_iso()

            subjects: list[dict[str, Any]] = []
            selections: list[dict[str, Any]] = []
            for item, revision, candidate, media_artifact in approved:
                reference_staged, summary = self._stage_reference_candidate(
                    project=project,
                    inventory_item=item,
                    candidate=candidate,
                    media_artifact=media_artifact,
                    now_iso=now,
                )
                staged.append(reference_staged)
                # Each immutable reference publication uses an artifact-specific
                # logical ID, so it has no predecessor. Promote it under the
                # same transaction as the Set/Approval pointer CAS. If any later
                # validation or CAS fails, rollback removes both the row state
                # and the staged file; a snapshot can therefore never reference
                # a committed non-current candidate artifact.
                self._promote_current_artifact(
                    logical_id=reference_staged.logical_id,
                    new_artifact_id=reference_staged.artifact_id,
                    expected_current_artifact_id=None,
                )
                subjects.append(
                    {
                        "anchor_type": item["anchor_type"],
                        "anchor_id": item["anchor_id"],
                        "label": item["label"],
                        "source_definition_hash": item["source_definition_hash"],
                        "prompt": revision.prompt,
                        "negative_prompt": revision.negative_prompt,
                        "candidates": [summary],
                        "selected_draft_reference_artifact_id": summary[
                            "reference_artifact_id"
                        ],
                    }
                )
                selections.append(
                    {
                        "anchor_type": item["anchor_type"],
                        "anchor_id": item["anchor_id"],
                        "source_definition_hash": item["source_definition_hash"],
                        "reference_artifact_id": summary["reference_artifact_id"],
                        "media_artifact_id": summary["media_artifact_id"],
                        "media_sha256": summary["media_sha256"],
                        "mime_type": summary["mime_type"],
                        "image_artifact_id": summary["media_artifact_id"],
                    }
                )

            set_id = self.artifacts.preallocate_artifact_id()
            set_payload = {
                "artifact_type": "style_bible_visual_reference_set",
                "schema_version": "2.0",
                "project_id": project.project_id,
                "visual_reference_set_artifact_id": set_id,
                "parent_visual_reference_set_artifact_id": expected_visual_reference_set_artifact_id,
                "source_script_revision_id": project.script_artifact_id,
                "source_script_analysis_revision_id": project.script_analysis_artifact_id,
                "source_style_bible_revision_id": project.style_bible_artifact_id,
                "status": "draft_ready",
                "subjects": subjects,
                "created_at": now,
            }
            self.validator.validate(
                "visual_reference_set_v1.schema.json",
                set_payload,
                error_code="VISUAL_REFERENCE_SET_INVALID",
            )
            set_staged = self.artifacts.stage_artifact_revision(
                logical_id=set_logical_id,
                artifact_type=ArtifactType.JSON,
                artifact_role=ArtifactRole.METADATA_FILE,
                filename="visual_reference_set.json",
                artifact_id=set_id,
                content=_json_bytes(set_payload),
                provider_source="visual_reference_v2_publication",
                content_hash=str(project.style_bible_artifact_id),
            )
            staged.append(set_staged)

            approval_id = self.artifacts.preallocate_artifact_id()
            approval_payload = {
                "artifact_type": "style_bible_visual_reference_approval_set",
                "schema_version": "2.0",
                "project_id": project.project_id,
                "approval_set_artifact_id": approval_id,
                "parent_approval_set_artifact_id": expected_approval_set_artifact_id,
                "source_script_revision_id": project.script_artifact_id,
                "source_script_analysis_revision_id": project.script_analysis_artifact_id,
                "source_style_bible_revision_id": project.style_bible_artifact_id,
                "source_visual_reference_set_artifact_id": set_id,
                "selections": selections,
                "created_at": now,
            }
            self.validator.validate(
                "visual_reference_approval_set_v1.schema.json",
                approval_payload,
                error_code="VISUAL_REFERENCE_APPROVAL_SET_INVALID",
            )
            approval_staged = self.artifacts.stage_artifact_revision(
                logical_id=approval_logical_id,
                artifact_type=ArtifactType.JSON,
                artifact_role=ArtifactRole.METADATA_FILE,
                filename="visual_reference_approvals.json",
                artifact_id=approval_id,
                content=_json_bytes(approval_payload),
                provider_source="visual_reference_v2_publication",
                content_hash=set_id,
            )
            staged.append(approval_staged)

            guard = StyleBibleVisualReferenceSetCommitGuard(
                script_artifact_id=str(project.script_artifact_id),
                script_analysis_artifact_id=str(project.script_analysis_artifact_id),
                style_bible_artifact_id=str(project.style_bible_artifact_id),
                previous_visual_reference_set_artifact_id=expected_visual_reference_set_artifact_id,
            )
            if not self.projects.compare_and_set_style_bible_visual_reference_set_with_carried_approval(
                project_id=project.project_id,
                new_visual_reference_set_artifact_id=set_id,
                new_approval_set_artifact_id=approval_id,
                previous_approval_set_artifact_id=expected_approval_set_artifact_id,
                guard=guard,
                updated_at=now,
            ):
                raise AppError(
                    "VISUAL_REFERENCE_PUBLICATION_CONFLICT",
                    "Visual Reference sources or publication pointers changed before publication committed.",
                    409,
                    retryable=True,
                    details={"project_id": project.project_id},
                )

            self._promote_current_artifact(
                logical_id=set_staged.logical_id,
                new_artifact_id=set_id,
                expected_current_artifact_id=expected_set_artifact_current_id,
            )
            self._promote_current_artifact(
                logical_id=approval_staged.logical_id,
                new_artifact_id=approval_id,
                expected_current_artifact_id=expected_approval_artifact_current_id,
            )
            self.session.commit()
        except Exception:
            rollback_safely(self.session, logger=logger)
            for item in staged:
                cleanup_staged_artifact_safely(item, logger=logger)
            raise

        self.session.expire_all()
        return {
            "project_id": project_id,
            "visual_reference_set_artifact_id": set_id,
            "approval_set_artifact_id": approval_id,
            "published_anchor_count": len(subjects),
            "ready": True,
        }

    def _approved_snapshot(
        self,
        project: Project,
        inventory: list[dict[str, Any]],
    ) -> list[
        tuple[
            dict[str, Any],
            VisualReferencePromptRevision,
            VisualReferenceCandidate,
            Artifact,
        ]
    ]:
        active_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(VisualReferenceWorkingOperation)
                .where(
                    VisualReferenceWorkingOperation.project_id == project.project_id,
                    VisualReferenceWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                )
            )
            or 0
        )
        if active_count:
            raise AppError(
                "VISUAL_REFERENCE_PUBLICATION_ACTIVE_OPERATION",
                "Visual References cannot be published while generation is active.",
                409,
                retryable=True,
                details={"active_operation_count": active_count},
            )

        snapshot: list[
            tuple[
                dict[str, Any],
                VisualReferencePromptRevision,
                VisualReferenceCandidate,
                Artifact,
            ]
        ] = []
        missing: list[str] = []
        for item in inventory:
            anchor_type = str(item["anchor_type"])
            anchor_id = str(item["anchor_id"])
            key = f"{anchor_type}:{anchor_id}"
            state = self.session.get(
                VisualReferenceAnchorState,
                (project.project_id, anchor_type, anchor_id),
            )
            if (
                state is None
                or state.active_operation_anchor_id is not None
                or state.resolution_status != "approved"
                or state.review_status != "approved"
                or state.approved_candidate_id is None
                or state.selected_candidate_id != state.approved_candidate_id
            ):
                missing.append(key)
                continue
            revision = self.session.get(
                VisualReferencePromptRevision,
                state.current_prompt_revision_id,
            )
            candidate = self.session.get(
                VisualReferenceCandidate,
                state.approved_candidate_id,
            )
            if (
                revision is None
                or revision.project_id != project.project_id
                or revision.anchor_type != anchor_type
                or revision.anchor_id != anchor_id
                or revision.source_style_bible_artifact_id != project.style_bible_artifact_id
                or revision.source_definition_hash != item["source_definition_hash"]
                or candidate is None
                or candidate.project_id != project.project_id
                or candidate.anchor_type != anchor_type
                or candidate.anchor_id != anchor_id
            ):
                missing.append(key)
                continue
            provenance = dict(candidate.provenance_json or {})
            if (
                provenance.get("source_style_bible_artifact_id") != project.style_bible_artifact_id
                or provenance.get("source_definition_hash") != item["source_definition_hash"]
            ):
                missing.append(key)
                continue
            media_artifact = self.session.get(Artifact, candidate.media_artifact_id)
            if (
                media_artifact is None
                or media_artifact.status != ArtifactStatus.READY
                or media_artifact.deleted_at is not None
                or media_artifact.artifact_type != ArtifactType.IMAGE
                or not media_artifact.logical_id.startswith(
                    f"project/{project.project_id}/visual_references/"
                )
                or not media_artifact.sha256
                or media_artifact.sha256 != candidate.media_sha256
                or not media_artifact.mime_type
                or media_artifact.mime_type != candidate.mime_type
            ):
                missing.append(key)
                continue
            snapshot.append((item, revision, candidate, media_artifact))
        if missing or len(snapshot) != len(inventory):
            raise AppError(
                "VISUAL_REFERENCE_PUBLICATION_NOT_READY",
                "Every current Style Bible Visual Reference anchor must have an approved compatible candidate before publication.",
                409,
                retryable=False,
                details={
                    "required_count": len(inventory),
                    "ready_count": len(snapshot),
                    "missing_anchor_keys": missing,
                },
            )
        return snapshot

    def _stage_reference_candidate(
        self,
        *,
        project: Project,
        inventory_item: dict[str, Any],
        candidate: VisualReferenceCandidate,
        media_artifact: Artifact,
        now_iso: str,
    ) -> tuple[StagedArtifact, dict[str, Any]]:
        reference_id = self.artifacts.preallocate_artifact_id()
        media_task_id = candidate.media_task_id or generate_id("media_task")
        operation_id = generate_id("operation")
        file_url = self.artifacts.build_download_descriptor(media_artifact.artifact_id)[
            "download_url"
        ]
        payload = {
            "artifact_type": "style_bible_visual_reference",
            "schema_version": "2.0",
            "reference_artifact_id": reference_id,
            "project_id": project.project_id,
            "source_script_revision_id": project.script_artifact_id,
            "source_script_analysis_revision_id": project.script_analysis_artifact_id,
            "source_style_bible_revision_id": project.style_bible_artifact_id,
            "operation_id": operation_id,
            "anchor_type": inventory_item["anchor_type"],
            "anchor_id": inventory_item["anchor_id"],
            "source_definition_hash": inventory_item["source_definition_hash"],
            "media_artifact_id": media_artifact.artifact_id,
            "media_task_id": media_task_id,
            "candidate_index": int(candidate.candidate_index),
            "mime_type": candidate.mime_type,
            "media_sha256": candidate.media_sha256,
            "file_url": file_url,
            "created_at": now_iso,
        }
        self.validator.validate(
            "visual_reference_candidate_v1.schema.json",
            payload,
            error_code="VISUAL_REFERENCE_CANDIDATE_INVALID",
        )
        staged = self.artifacts.stage_artifact_revision(
            logical_id=(
                f"project/{project.project_id}/visual_references/candidates/{reference_id}"
            ),
            artifact_type=ArtifactType.JSON,
            artifact_role=ArtifactRole.METADATA_FILE,
            filename="visual_reference.json",
            artifact_id=reference_id,
            content=_json_bytes(payload),
            provider_source="visual_reference_v2_publication",
            content_hash=candidate.media_sha256,
        )
        return staged, {
            "reference_artifact_id": reference_id,
            "media_artifact_id": media_artifact.artifact_id,
            "media_task_id": media_task_id,
            "candidate_index": int(candidate.candidate_index),
            "mime_type": candidate.mime_type,
            "media_sha256": candidate.media_sha256,
            "file_url": file_url,
        }

    def _current_inventory(self, project: Project) -> list[dict[str, Any]]:
        if (
            project.script_artifact_id is None
            or project.script_analysis_artifact_id is None
            or project.style_bible_artifact_id is None
        ):
            raise AppError(
                "VISUAL_REFERENCE_NOT_READY",
                "Current Script, Script Analysis, and Style Bible are required for publication.",
                409,
                retryable=False,
            )
        style = self.style_loader.load_text_approved(project, str(project.style_bible_artifact_id))
        inventory = build_visual_reference_inventory(style)
        if not inventory:
            raise AppError(
                "VISUAL_REFERENCE_INVENTORY_EMPTY",
                "The current Style Bible has no Visual Reference anchors to publish.",
                409,
                retryable=False,
            )
        return inventory

    def _promote_current_artifact(
        self,
        *,
        logical_id: str,
        new_artifact_id: str,
        expected_current_artifact_id: str | None,
    ) -> None:
        if not self.artifact_rows.compare_and_set_current_artifact(
            logical_id=logical_id,
            new_artifact_id=new_artifact_id,
            expected_current_artifact_id=expected_current_artifact_id,
        ):
            raise AppError(
                "VISUAL_REFERENCE_PUBLICATION_CONFLICT",
                "Visual Reference publication artifact current pointer changed before publication committed.",
                409,
                retryable=True,
                details={"logical_id": logical_id},
            )

    @staticmethod
    def _assert_expected_pointers(
        project: Project,
        *,
        expected_visual_reference_set_artifact_id: str | None,
        expected_approval_set_artifact_id: str | None,
    ) -> None:
        if (
            project.style_bible_visual_reference_set_artifact_id
            != expected_visual_reference_set_artifact_id
            or project.style_bible_visual_reference_approval_set_artifact_id
            != expected_approval_set_artifact_id
        ):
            raise AppError(
                "VISUAL_REFERENCE_PUBLICATION_CONFLICT",
                "Expected Visual Reference publication pointers are no longer current.",
                409,
                retryable=True,
                details={
                    "expected_visual_reference_set_artifact_id": expected_visual_reference_set_artifact_id,
                    "current_visual_reference_set_artifact_id": project.style_bible_visual_reference_set_artifact_id,
                    "expected_approval_set_artifact_id": expected_approval_set_artifact_id,
                    "current_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
                },
            )

    def _project_required(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError(
                "PROJECT_NOT_FOUND",
                f"Project '{project_id}' was not found.",
                404,
                retryable=False,
            )
        return project
