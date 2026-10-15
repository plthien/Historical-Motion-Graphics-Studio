from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.modules.v4.scene_asset_working_state import SceneAssetWorkingStateService


class SceneAssetPromptRevisionService:
    """Per-beat prompt identity authority for Scene Assets V2.

    Prompt Bundle remains the immutable aggregate artifact and authoring source.
    A PromptRevision is the beat-local identity used for optimistic concurrency,
    execution freezing, and candidate lineage. Unrelated Prompt Bundle edits do
    not change a beat's current PromptRevision.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._current_cache: dict[tuple[str, str], SceneAssetPromptRevision] = {}
        self.working_state = SceneAssetWorkingStateService(session)

    def prime_current(self, project_id: str, beat_ids: Iterable[str]) -> list[SceneAssetPromptRevision]:
        unique_ids = sorted({str(beat_id) for beat_id in beat_ids if beat_id})
        if not unique_ids:
            return []
        revisions = list(
            self.session.scalars(
                select(SceneAssetPromptRevision).where(
                    SceneAssetPromptRevision.project_id == project_id,
                    SceneAssetPromptRevision.beat_id.in_(unique_ids),
                    SceneAssetPromptRevision.is_current == 1,
                )
            )
        )
        for revision in revisions:
            self._current_cache[(project_id, str(revision.beat_id))] = revision
        return revisions

    def current(self, project_id: str, beat_id: str) -> SceneAssetPromptRevision:
        cache_key = (project_id, beat_id)
        cached = self._current_cache.get(cache_key)
        if cached is not None and cached.is_current:
            return cached
        revision = self.session.scalar(
            select(SceneAssetPromptRevision).where(
                SceneAssetPromptRevision.project_id == project_id,
                SceneAssetPromptRevision.beat_id == beat_id,
                SceneAssetPromptRevision.is_current == 1,
            )
        )
        if revision is None:
            raise AppError(
                "SCENE_ASSET_PROMPT_REVISION_NOT_FOUND",
                "The beat does not have a current Scene Asset PromptRevision.",
                409,
                retryable=False,
                details={"project_id": project_id, "beat_id": beat_id},
            )
        self._current_cache[cache_key] = revision
        return revision

    def assert_current(
        self,
        project_id: str,
        beat_id: str,
        expected_prompt_revision_id: str,
    ) -> SceneAssetPromptRevision:
        revision = self.current(project_id, beat_id)
        if revision.prompt_revision_id != expected_prompt_revision_id:
            raise AppError(
                "SCENE_ASSET_PROMPT_REVISION_CONFLICT",
                "The beat prompt changed since the client loaded it.",
                409,
                retryable=True,
                details={
                    "project_id": project_id,
                    "beat_id": beat_id,
                    "expected_prompt_revision_id": expected_prompt_revision_id,
                    "current_prompt_revision_id": revision.prompt_revision_id,
                },
            )
        return revision

    def publish_approved_bundle(self, project_id: str, bundle: dict[str, Any]) -> list[str]:
        if bundle.get("status") != "approved":
            return []
        bundle_id = str(bundle.get("prompt_bundle_revision_id") or "")
        if not bundle_id:
            raise AppError(
                "SCENE_ASSET_PROMPT_REVISION_INVALID",
                "Approved Prompt Bundle is missing its revision identity.",
                409,
                retryable=False,
            )
        operation = str(bundle.get("revision_operation") or "")
        target_beat_id = bundle.get("operation_target_beat_id")
        if operation in {"manual_edit", "beat_recompilation"}:
            if not isinstance(target_beat_id, str) or not target_beat_id:
                raise AppError(
                    "SCENE_ASSET_PROMPT_REVISION_INVALID",
                    "Beat-local Prompt Bundle revision is missing its target beat.",
                    409,
                    retryable=False,
                )
            targets = [target_beat_id]
        else:
            targets = [str(item.get("beat_id") or "") for item in bundle.get("prompt_items", [])]

        items = {
            str(item.get("beat_id") or ""): item
            for item in bundle.get("prompt_items", [])
            if isinstance(item, dict) and item.get("beat_id")
        }
        if not targets or any(beat_id not in items for beat_id in targets):
            raise AppError(
                "SCENE_ASSET_PROMPT_REVISION_INVALID",
                "Approved Prompt Bundle does not contain every PromptRevision target beat.",
                409,
                retryable=False,
                details={"target_beat_ids": targets},
            )

        if operation not in {"manual_edit", "beat_recompilation"}:
            self.working_state.reset_for_full_prompt_bundle_epoch(
                project_id=project_id,
                prompt_items=list(items.values()),
                now_iso=utc_now_iso(),
            )

        created: list[str] = []
        now = utc_now_iso()
        for beat_id in targets:
            item = items[beat_id]
            current = self.session.scalar(
                select(SceneAssetPromptRevision).where(
                    SceneAssetPromptRevision.project_id == project_id,
                    SceneAssetPromptRevision.beat_id == beat_id,
                    SceneAssetPromptRevision.is_current == 1,
                )
            )
            if current is not None:
                current.is_current = False
            max_revision = self.session.scalar(
                select(func.max(SceneAssetPromptRevision.revision_no)).where(
                    SceneAssetPromptRevision.project_id == project_id,
                    SceneAssetPromptRevision.beat_id == beat_id,
                )
            )
            revision_kind = (
                "manual_edit" if operation == "manual_edit"
                else "restore_compiled" if operation == "beat_recompilation"
                else "compiled"
            )
            revision = SceneAssetPromptRevision(
                prompt_revision_id=generate_id("scene_asset_prompt_revision"),
                project_id=project_id,
                scene_id=str(item["scene_id"]),
                beat_id=beat_id,
                revision_no=int(max_revision or 0) + 1,
                revision_kind=revision_kind,
                is_current=True,
                source_prompt_bundle_artifact_id=bundle_id,
                source_prompt_item_id=str(item["prompt_item_id"]),
                source_prompt_hash=str(item["prompt_hash"]),
                image_prompt=str(item["image_prompt"]),
                negative_prompt=str(item["negative_prompt"]),
                created_at=now,
            )
            self.session.add(revision)
            self.session.flush()
            self._current_cache[(project_id, beat_id)] = revision
            created.append(revision.prompt_revision_id)
        return created
