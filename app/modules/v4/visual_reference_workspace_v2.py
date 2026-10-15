from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.models import Project
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.v4.style_bible_consumer_loader import StyleBibleConsumerLoaderV4
from app.modules.v4.visual_reference_prompt_compiler import build_visual_reference_inventory
from app.repositories import ArtifactRepository, ProjectRepository
from app.repositories.visual_reference_working import ACTIVE_OPERATION_STATUSES
from app.services.artifacts import ArtifactService


class VisualReferenceWorkspaceV2Service:
    """Read-only V2 workspace/execution projections over durable anchor state."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifacts = ArtifactService(session, ArtifactRepository(session))
        self.style_loader = StyleBibleConsumerLoaderV4(self.artifacts)
        self.api_base_path = get_settings().api_base_path

    def workspace(self, project_id: str) -> dict[str, Any]:
        project, inventory = self._project_inventory(project_id)
        states = {
            (item.anchor_type, item.anchor_id): item
            for item in self.session.scalars(
                select(VisualReferenceAnchorState).where(
                    VisualReferenceAnchorState.project_id == project_id
                )
            )
        }
        revisions = {
            (item.anchor_type, item.anchor_id): item
            for item in self.session.scalars(
                select(VisualReferencePromptRevision).where(
                    VisualReferencePromptRevision.project_id == project_id,
                    VisualReferencePromptRevision.is_current == 1,
                )
            )
        }
        candidates_by_key: dict[tuple[str, str], list[VisualReferenceCandidate]] = defaultdict(list)
        for candidate in self.session.scalars(
            select(VisualReferenceCandidate)
            .where(VisualReferenceCandidate.project_id == project_id)
            .order_by(
                VisualReferenceCandidate.anchor_type,
                VisualReferenceCandidate.anchor_id,
                VisualReferenceCandidate.created_at,
                VisualReferenceCandidate.candidate_index,
                VisualReferenceCandidate.candidate_id,
            )
        ):
            candidates_by_key[(candidate.anchor_type, candidate.anchor_id)].append(candidate)
        active_operation_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(VisualReferenceWorkingOperation)
                .where(
                    VisualReferenceWorkingOperation.project_id == project_id,
                    VisualReferenceWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                )
            )
            or 0
        )

        anchors = [
            self._project_anchor(
                project=project,
                inventory_item=item,
                state=states.get((str(item["anchor_type"]), str(item["anchor_id"]))),
                revision=revisions.get((str(item["anchor_type"]), str(item["anchor_id"]))),
                candidates=candidates_by_key.get(
                    (str(item["anchor_type"]), str(item["anchor_id"])), []
                ),
            )
            for item in inventory
        ]
        missing: list[str] = []
        stale: list[str] = []
        approved_count = 0
        for anchor in anchors:
            key = f"{anchor['anchor_type']}:{anchor['anchor_id']}"
            if anchor["upstream_stale"]:
                stale.append(key)
            if (
                not anchor["upstream_stale"]
                and anchor["resolution_status"] == "approved"
                and anchor["approved_candidate_id"] is not None
            ):
                approved_count += 1
            else:
                missing.append(key)
        return {
            "project_id": project_id,
            "style_bible_artifact_id": project.style_bible_artifact_id,
            "published_visual_reference_set_artifact_id": (
                project.style_bible_visual_reference_set_artifact_id
            ),
            "published_approval_set_artifact_id": (
                project.style_bible_visual_reference_approval_set_artifact_id
            ),
            "generated": any(anchor["candidates"] for anchor in anchors),
            "anchors": anchors,
            "readiness": {
                "required_count": len(anchors),
                "approved_count": approved_count,
                "ready": bool(anchors) and approved_count == len(anchors),
                "missing_anchor_keys": missing,
                "stale_anchor_keys": stale,
            },
            "active_operation_count": active_operation_count,
        }

    def anchor(self, project_id: str, anchor_type: str, anchor_id: str) -> dict[str, Any]:
        project, inventory = self._project_inventory(project_id)
        item = next(
            (
                value
                for value in inventory
                if value["anchor_type"] == anchor_type and value["anchor_id"] == anchor_id
            ),
            None,
        )
        if item is None:
            raise AppError(
                "VISUAL_REFERENCE_ANCHOR_NOT_FOUND",
                "The Visual Reference anchor was not found in the current Style Bible.",
                404,
                retryable=False,
                details={"anchor_type": anchor_type, "anchor_id": anchor_id},
            )
        state = self.session.get(
            VisualReferenceAnchorState,
            (project_id, anchor_type, anchor_id),
        )
        revision = self.session.scalar(
            select(VisualReferencePromptRevision).where(
                VisualReferencePromptRevision.project_id == project_id,
                VisualReferencePromptRevision.anchor_type == anchor_type,
                VisualReferencePromptRevision.anchor_id == anchor_id,
                VisualReferencePromptRevision.is_current == 1,
            )
        )
        candidates = list(
            self.session.scalars(
                select(VisualReferenceCandidate)
                .where(
                    VisualReferenceCandidate.project_id == project_id,
                    VisualReferenceCandidate.anchor_type == anchor_type,
                    VisualReferenceCandidate.anchor_id == anchor_id,
                )
                .order_by(
                    VisualReferenceCandidate.created_at,
                    VisualReferenceCandidate.candidate_index,
                    VisualReferenceCandidate.candidate_id,
                )
            )
        )
        return self._project_anchor(
            project=project,
            inventory_item=item,
            state=state,
            revision=revision,
            candidates=candidates,
        )

    def executions(self, project_id: str, *, active_only: bool = True) -> dict[str, Any]:
        self._project_required(project_id)
        operation_stmt = select(VisualReferenceWorkingOperation).where(
            VisualReferenceWorkingOperation.project_id == project_id
        )
        if active_only:
            operation_stmt = operation_stmt.where(
                VisualReferenceWorkingOperation.status.in_(ACTIVE_OPERATION_STATUSES)
            )
        operations = list(
            self.session.scalars(
                operation_stmt.order_by(
                    VisualReferenceWorkingOperation.created_at.desc(),
                    VisualReferenceWorkingOperation.operation_id.desc(),
                )
            )
        )
        operation_ids = [item.operation_id for item in operations]
        if not operation_ids:
            return {"project_id": project_id, "executions": []}

        units = list(
            self.session.scalars(
                select(VisualReferenceOperationAnchor)
                .where(VisualReferenceOperationAnchor.operation_id.in_(operation_ids))
                .order_by(
                    VisualReferenceOperationAnchor.operation_id,
                    VisualReferenceOperationAnchor.anchor_type,
                    VisualReferenceOperationAnchor.anchor_id,
                )
            )
        )
        unit_ids = [item.operation_anchor_id for item in units]
        tasks = (
            list(
                self.session.scalars(
                    select(VisualReferenceOperationTask)
                    .where(VisualReferenceOperationTask.operation_anchor_id.in_(unit_ids))
                    .order_by(
                        VisualReferenceOperationTask.operation_anchor_id,
                        VisualReferenceOperationTask.candidate_index,
                    )
                )
            )
            if unit_ids
            else []
        )
        tasks_by_unit: dict[str, list[VisualReferenceOperationTask]] = defaultdict(list)
        for task in tasks:
            tasks_by_unit[task.operation_anchor_id].append(task)
        units_by_operation: dict[str, list[VisualReferenceOperationAnchor]] = defaultdict(list)
        for unit in units:
            units_by_operation[unit.operation_id].append(unit)

        return {
            "project_id": project_id,
            "executions": [
                {
                    "operation_id": operation.operation_id,
                    "operation_type": operation.operation_type,
                    "status": operation.status,
                    "total_anchor_count": int(operation.total_anchor_count),
                    "completed_count": int(operation.completed_count),
                    "failed_count": int(operation.failed_count),
                    "cancelled_count": int(operation.cancelled_count),
                    "next_reconcile_at": operation.next_reconcile_at,
                    "created_at": operation.created_at,
                    "updated_at": operation.updated_at,
                    "error_code": operation.error_code,
                    "error_message": operation.error_message,
                    "anchors": [
                        {
                            "operation_anchor_id": unit.operation_anchor_id,
                            "anchor_type": unit.anchor_type,
                            "anchor_id": unit.anchor_id,
                            "status": unit.status,
                            "candidate_count": int(unit.candidate_count),
                            "tasks": [
                                {
                                    "operation_task_id": task.operation_task_id,
                                    "candidate_index": int(task.candidate_index),
                                    "status": task.status,
                                    "media_task_id": task.media_task_id,
                                    "next_reconcile_at": task.next_reconcile_at,
                                    "error_code": task.error_code,
                                    "error_message": task.error_message,
                                }
                                for task in tasks_by_unit.get(unit.operation_anchor_id, [])
                            ],
                        }
                        for unit in units_by_operation.get(operation.operation_id, [])
                    ],
                }
                for operation in operations
            ],
        }

    def _project_anchor(
        self,
        *,
        project: Project,
        inventory_item: dict[str, Any],
        state: VisualReferenceAnchorState | None,
        revision: VisualReferencePromptRevision | None,
        candidates: list[VisualReferenceCandidate],
    ) -> dict[str, Any]:
        anchor_type = str(inventory_item["anchor_type"])
        anchor_id = str(inventory_item["anchor_id"])
        source_definition_hash = str(inventory_item["source_definition_hash"])
        stale = bool(
            revision is not None
            and (
                revision.source_style_bible_artifact_id != project.style_bible_artifact_id
                or revision.source_definition_hash != source_definition_hash
            )
        )
        candidate_payloads = [
            self._candidate_payload(
                candidate,
                current_style_bible_artifact_id=project.style_bible_artifact_id,
                current_source_definition_hash=source_definition_hash,
            )
            for candidate in candidates
        ]
        prompt = (
            revision.prompt if revision is not None else str(inventory_item.get("prompt", ""))
        )
        negative_prompt = (
            revision.negative_prompt
            if revision is not None
            else str(inventory_item.get("negative_prompt", ""))
        )
        return {
            "anchor_type": anchor_type,
            "anchor_id": anchor_id,
            "label": str(inventory_item.get("label") or anchor_id),
            "source_definition_hash": source_definition_hash,
            "version": int(state.version) if state is not None else 0,
            "initialized": state is not None and revision is not None,
            "upstream_stale": stale,
            "resolution_status": (
                str(state.resolution_status) if state is not None else "not_generated"
            ),
            "review_status": str(state.review_status) if state is not None else "unreviewed",
            "prompt_revision_id": revision.prompt_revision_id if revision is not None else None,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "candidates": candidate_payloads,
            "selected_candidate_id": state.selected_candidate_id if state is not None else None,
            "approved_candidate_id": state.approved_candidate_id if state is not None else None,
            "active_operation_anchor_id": (
                state.active_operation_anchor_id if state is not None else None
            ),
            "last_error": state.last_error_json if state is not None else None,
            "last_progress_at": state.last_progress_at if state is not None else None,
        }

    def _candidate_payload(
        self,
        candidate: VisualReferenceCandidate,
        *,
        current_style_bible_artifact_id: str | None,
        current_source_definition_hash: str,
    ) -> dict[str, Any]:
        provenance = dict(candidate.provenance_json or {})
        compatible = bool(
            current_style_bible_artifact_id is not None
            and provenance.get("source_style_bible_artifact_id")
            == current_style_bible_artifact_id
            and provenance.get("source_definition_hash")
            == current_source_definition_hash
        )
        return {
            "candidate_id": candidate.candidate_id,
            "source_prompt_revision_id": candidate.source_prompt_revision_id,
            "candidate_index": int(candidate.candidate_index),
            "media_artifact_id": candidate.media_artifact_id,
            "media_task_id": candidate.media_task_id,
            "mime_type": candidate.mime_type,
            "media_sha256": candidate.media_sha256,
            "file_url": (
                f"{self.api_base_path}/artifacts/{candidate.media_artifact_id}/content"
            ),
            "source_kind": str(provenance.get("source_kind") or "unknown"),
            "compatible_with_current_definition": compatible,
            "created_at": candidate.created_at,
        }

    def _project_inventory(self, project_id: str) -> tuple[Project, list[dict[str, Any]]]:
        project = self._project_required(project_id)
        if project.style_bible_artifact_id is None:
            return project, []
        style = self.style_loader.load_text_approved(project, project.style_bible_artifact_id)
        return project, build_visual_reference_inventory(style)

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
