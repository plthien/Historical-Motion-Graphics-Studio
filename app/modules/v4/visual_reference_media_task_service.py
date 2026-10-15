from __future__ import annotations

from pathlib import Path

from app.api.errors import AppError
from app.modules.media.media_task_service import MediaTaskService


class VisualReferenceMediaTaskService(MediaTaskService):
    """MediaTask adapter that fences Visual Reference V2 storage namespaces.

    The transport engine remains shared with Scene Assets, but each domain owns
    its project-scoped storage root. This prevents a Visual Reference command
    from being accepted into the Scene Asset namespace (or vice versa) without
    duplicating provider/task lifecycle logic.
    """

    def _validated_namespace_path(
        self,
        value: str,
        *,
        project_id: str,
        purpose: str | None = None,
    ) -> Path:
        parts = Path(value).parts
        if not parts:
            raise AppError(
                "MEDIA_IMAGE_REQUEST_INVALID",
                "Visual Reference storage namespace is invalid.",
                422,
                retryable=False,
            )
        for part in parts:
            self.safe_path_segment(str(part), "storage_namespace")
        expected = (
            "project",
            self.safe_path_segment(project_id, "project_id"),
            "visual_references",
        )
        if tuple(parts[:3]) != expected:
            raise AppError(
                "MEDIA_IMAGE_REQUEST_INVALID",
                "Visual Reference storage namespace is not project-scoped.",
                422,
                retryable=False,
            )
        return Path(*parts)
