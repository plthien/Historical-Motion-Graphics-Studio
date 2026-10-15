from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.core.ids import generate_id, validate_artifact_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Artifact
from app.repositories import ArtifactRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PagedArtifacts:
    items: list[Artifact]
    page: int
    page_size: int
    total: int


@dataclass(slots=True)
class StagedArtifact:
    artifact: Artifact
    artifact_id: str
    logical_id: str
    filepath: Path
    attempt_dir: Path | None = None
    owns_file: bool = True

    def cleanup(self) -> None:
        """Delete only filesystem state exclusively created by this attempt.

        Artifact files are published below a UUID attempt directory created
        with ``exist_ok=False``.  No competing writer can share this path, so
        rollback cleanup cannot unlink another transaction's winning file.
        """
        if not self.owns_file:
            return
        if self.filepath.exists():
            self.filepath.unlink()
        if self.attempt_dir is not None and self.attempt_dir.exists():
            try:
                self.attempt_dir.rmdir()
            except OSError:
                # Never recursively delete: an unexpected sibling means this
                # attempt no longer has proof of exclusive directory contents.
                pass


def rollback_safely(session: Session, *, logger: logging.Logger) -> None:
    """Attempt session rollback without raising. Rollback failures are logged.
    The caller's original exception is preserved by using bare ``raise`` after calling this helper."""
    try:
        session.rollback()
    except Exception:
        logger.exception("Failed to rollback artifact transaction")


def cleanup_staged_artifact_safely(staged: StagedArtifact | None, *, logger: logging.Logger) -> None:
    """Attempt to delete the staged artifact file without raising. Cleanup failures are logged.
    Only the newly staged file may be deleted. Historical files are never touched."""
    if staged is None:
        return
    try:
        staged.cleanup()
    except Exception:
        logger.exception(
            "Failed to clean staged artifact file",
            extra={
                "artifact_id": staged.artifact_id,
                "logical_id": staged.logical_id,
                "filepath": str(staged.filepath),
            },
        )



def _artifact_integrity_error(
    exc: IntegrityError,
    *,
    artifact_id: str,
    logical_id: str,
    version_no: int,
) -> AppError:
    """Classify the bounded SQLite uniqueness failures owned by Artifact.

    SQLite does not expose named constraints through DB-API metadata.  The
    supported local baseline therefore uses a deliberately narrow inspection
    of SQLite's canonical column list and treats every other integrity failure
    as persistence failure.  Tests cover all three branches.
    """
    message = str(getattr(exc, "orig", exc)).lower().replace('"', "")
    if "unique constraint failed: artifacts.artifact_id" in message:
        return AppError(
            "ARTIFACT_ID_CONFLICT",
            "Artifact identifier already exists.",
            409,
            retryable=False,
            details={"artifact_id": artifact_id},
        )
    if (
        "unique constraint failed: artifacts.logical_id, artifacts.version_no" in message
        or "ux_artifacts_logical_version" in message
    ):
        return AppError(
            "ARTIFACT_VERSION_CONFLICT",
            "Artifact logical version was published by another writer.",
            409,
            retryable=False,
            details={"logical_id": logical_id, "version_no": version_no},
        )
    return AppError(
        "ARTIFACT_PERSISTENCE_FAILED",
        "Artifact metadata could not be persisted.",
        500,
        retryable=False,
        details={"artifact_id": artifact_id, "logical_id": logical_id},
    )

def _unlink_safely(path: Path, *, logger: logging.Logger, reason: str) -> None:
    """Attempt to unlink a file without raising. Failures are logged."""
    try:
        if path.exists():
            path.unlink()
    except Exception:
        logger.exception(
            "Failed to remove artifact file during failed staging",
            extra={
                "filepath": str(path),
                "reason": reason,
            },
        )


def _require_descendant_path(*, parent: Path, candidate: Path, field: str) -> Path:
    """Resolve an ordinary path and prove it remains below its owner path.

    This is lexical/runtime containment for values controlled by this write
    path. Deferred symlink, junction, and reparse-point hardening remains out
    of scope for the supported local single-user boundary.
    """

    resolved_parent = parent.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_parent)
    except ValueError as exc:
        raise AppError(
            "ARTIFACT_PATH_INVALID",
            "Artifact publication path is outside its configured owner path.",
            400,
            retryable=False,
            details={"field": field},
        ) from exc
    return resolved_candidate



class ArtifactService:
    def __init__(self, session: Session, repository: ArtifactRepository, settings: Settings | None = None) -> None:
        self.session = session
        self.repository = repository
        self.settings = settings or get_settings()

    def list_artifacts(
        self,
        *,
        page: int,
        page_size: int,
        sort: str,
        artifact_type: str | None,
        logical_id: str | None,
        is_current: bool | None,
    ) -> PagedArtifacts:
        offset = (page - 1) * page_size
        items = self.repository.list_all(
            artifact_type=artifact_type,
            logical_id=logical_id,
            is_current=is_current,
            sort=sort,
            offset=offset,
            limit=page_size,
        )
        total = self.repository.count_all(
            artifact_type=artifact_type,
            logical_id=logical_id,
            is_current=is_current,
        )
        return PagedArtifacts(items=items, page=page, page_size=page_size, total=total)

    def get_required(self, artifact_id: str) -> Artifact:
        artifact = self.repository.get(artifact_id)
        if artifact is None:
            raise AppError(
                "ARTIFACT_NOT_FOUND",
                f"Artifact '{artifact_id}' was not found.",
                404,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        return artifact

    def preallocate_artifact_id(self) -> str:
        return generate_id("artifact")

    def write_artifact(
        self,
        *,
        logical_id: str,
        artifact_type: ArtifactType,
        filename: str,
        content: bytes,
        artifact_id: str | None = None,
        artifact_role: ArtifactRole | None = None,
        source_job_id: str | None = None,
        provider_source: str | None = None,
        content_hash: str | None = None,
    ) -> StagedArtifact:
        """Write and immediately publish a generic Artifact revision.

        This preserves the established generic Artifact behavior. Domain owners
        that also maintain a canonical pointer must use ``stage_artifact_revision``
        and publish both sides atomically in their domain transaction.
        """
        return self._write_artifact_revision(
            logical_id=logical_id,
            artifact_type=artifact_type,
            filename=filename,
            content=content,
            artifact_id=artifact_id,
            artifact_role=artifact_role,
            source_job_id=source_job_id,
            provider_source=provider_source,
            content_hash=content_hash,
            publish_current=True,
            source_path=None,
        )

    def stage_artifact_revision(
        self,
        *,
        logical_id: str,
        artifact_type: ArtifactType,
        filename: str,
        content: bytes,
        artifact_id: str | None = None,
        artifact_role: ArtifactRole | None = None,
        source_job_id: str | None = None,
        provider_source: str | None = None,
        content_hash: str | None = None,
    ) -> StagedArtifact:
        """Persist an Artifact revision without making it externally current.

        The caller must either atomically publish the staged row with its domain
        pointer in the same transaction or roll the transaction back and clean the
        returned staged file.
        """
        return self._write_artifact_revision(
            logical_id=logical_id,
            artifact_type=artifact_type,
            filename=filename,
            content=content,
            artifact_id=artifact_id,
            artifact_role=artifact_role,
            source_job_id=source_job_id,
            provider_source=provider_source,
            content_hash=content_hash,
            publish_current=False,
            source_path=None,
        )


    def stage_artifact_file_revision(
        self,
        *,
        logical_id: str,
        artifact_type: ArtifactType,
        filename: str,
        source_path: Path,
        artifact_id: str | None = None,
        artifact_role: ArtifactRole | None = None,
        source_job_id: str | None = None,
        provider_source: str | None = None,
        content_hash: str | None = None,
    ) -> StagedArtifact:
        """Stage an immutable Artifact revision from a validated file.

        The source is copied in bounded chunks so large Render outputs are never
        loaded wholly into application memory. The caller retains ownership of
        the source attempt file and must publish or roll back this staged row.
        """
        return self._write_artifact_revision(
            logical_id=logical_id,
            artifact_type=artifact_type,
            filename=filename,
            content=None,
            artifact_id=artifact_id,
            artifact_role=artifact_role,
            source_job_id=source_job_id,
            provider_source=provider_source,
            content_hash=content_hash,
            publish_current=False,
            source_path=source_path,
        )

    def _write_artifact_revision(
        self,
        *,
        logical_id: str,
        artifact_type: ArtifactType,
        filename: str,
        content: bytes | None,
        artifact_id: str | None,
        artifact_role: ArtifactRole | None,
        source_job_id: str | None,
        provider_source: str | None,
        content_hash: str | None,
        publish_current: bool,
        source_path: Path | None,
    ) -> StagedArtifact:
        if source_path is None and not content:
            raise AppError("ARTIFACT_EMPTY", "Artifact content must not be empty.", 400, retryable=False)
        if source_path is not None and (not source_path.is_file() or source_path.is_symlink()):
            raise AppError("ARTIFACT_SOURCE_INVALID", "Artifact source must be an ordinary regular file.", 400, retryable=False)

        safe_name = Path(filename).name
        if safe_name != filename or not safe_name:
            raise AppError(
                "ARTIFACT_FILENAME_INVALID",
                "Artifact filename must be a simple file name.",
                400,
                retryable=False,
                details={"filename": filename},
            )

        resolved_artifact_id = artifact_id if artifact_id is not None else generate_id("artifact")
        try:
            validate_artifact_id(resolved_artifact_id)
        except ValueError as exc:
            raise AppError(
                "ARTIFACT_ID_INVALID",
                "Artifact identifier is invalid.",
                400,
                retryable=False,
                details={"field": "artifact_id"},
            ) from exc

        root = self._artifact_root()
        version_no = self.repository.get_next_version_no(logical_id)

        if artifact_id is not None and self.repository.get(artifact_id) is not None:
            raise AppError(
                "ARTIFACT_ID_CONFLICT",
                "Artifact identifier already exists.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )

        # Every attempt owns a distinct final directory. Even when two
        # sessions choose the same logical version (or the same explicit ID),
        # neither can overwrite or clean the other's bytes before the database
        # uniqueness constraint selects the winner. The public Artifact ID is
        # intentionally not duplicated into the filesystem directory name: the
        # database remains the authority mapping Artifact ID -> immutable path.
        # A 48-bit random attempt token is ample for per-version local write
        # isolation while keeping Windows/local artifact paths compact.
        attempt_token = uuid4().hex[:12]
        version_dir = _require_descendant_path(
            parent=root,
            candidate=root / logical_id / f"v{version_no}",
            field="logical_id",
        )
        attempt_dir = _require_descendant_path(
            parent=version_dir,
            candidate=version_dir / f"r-{attempt_token}",
            field="attempt_token",
        )
        temp_path = _require_descendant_path(
            parent=attempt_dir,
            candidate=attempt_dir / f".tmp-{safe_name}",
            field="filename",
        )
        final_path = _require_descendant_path(
            parent=attempt_dir,
            candidate=attempt_dir / safe_name,
            field="filename",
        )
        attempt_dir.mkdir(parents=True, exist_ok=False)

        try:
            if source_path is None:
                temp_path.write_bytes(content or b"")
            else:
                with source_path.open("rb") as source, temp_path.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            if temp_path.stat().st_size <= 0:
                raise AppError("ARTIFACT_EMPTY", "Artifact write produced an empty file.", 400, retryable=False)

            sha256 = self._sha256_for_path(temp_path)
            size_bytes = temp_path.stat().st_size
            mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
            temp_path.replace(final_path)

            now = utc_now_iso()
            artifact = Artifact(
                artifact_id=resolved_artifact_id,
                logical_id=logical_id,
                version_no=version_no,
                is_current=publish_current,
                superseded_by_artifact_id=None,
                artifact_type=artifact_type,
                artifact_role=artifact_role,
                path=final_path.relative_to(root).as_posix(),
                content_hash=content_hash,
                sha256=sha256,
                mime_type=mime_type,
                size_bytes=size_bytes,
                width=None,
                height=None,
                duration_sec=None,
                provider_result_url=None,
                provider_source=provider_source,
                source_job_id=source_job_id,
                status=ArtifactStatus.READY,
                created_at=now,
                deleted_at=None,
            )

            previous_current = (
                self.repository.get_current_by_logical_id(logical_id)
                if publish_current
                else None
            )
            self.repository.create(artifact)
            if previous_current is not None:
                previous_current.is_current = False
                previous_current.superseded_by_artifact_id = artifact.artifact_id

            self.session.flush()
            return StagedArtifact(
                artifact=artifact,
                artifact_id=artifact.artifact_id,
                logical_id=artifact.logical_id,
                filepath=final_path,
                attempt_dir=attempt_dir,
                owns_file=True,
            )
        except Exception as exc:
            _unlink_safely(temp_path, logger=logger, reason="staging_failure_temp_cleanup")
            _unlink_safely(final_path, logger=logger, reason="staging_failure_final_cleanup")
            try:
                attempt_dir.rmdir()
            except OSError:
                pass
            if isinstance(exc, IntegrityError):
                raise _artifact_integrity_error(
                    exc,
                    artifact_id=resolved_artifact_id,
                    logical_id=logical_id,
                    version_no=version_no,
                ) from exc
            raise


    def build_download_descriptor(self, artifact_id: str) -> dict[str, str | None]:
        artifact = self.get_required(artifact_id)
        self.resolve_file_path(artifact)
        return {
            "artifact_id": artifact.artifact_id,
            "download_url": f"{self.settings.api_base_path}/artifacts/{artifact.artifact_id}/content",
            "expires_at": None,
            "sha256": artifact.sha256,
        }

    def read_artifact_bytes(self, artifact_id: str) -> bytes:
        artifact = self.get_required(artifact_id)
        path = self.resolve_file_path(artifact)
        return path.read_bytes()

    def resolve_file_path(self, artifact: Artifact) -> Path:
        root = self._artifact_root()
        stored_path = Path(artifact.path)
        resolved = stored_path.resolve() if stored_path.is_absolute() else (root / stored_path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise AppError(
                "ARTIFACT_PATH_INVALID",
                "Artifact path is outside the configured artifact root.",
                400,
                retryable=False,
                details={"artifact_id": artifact.artifact_id, "path": artifact.path},
            ) from exc
        if not resolved.is_file():
            raise AppError(
                "ARTIFACT_FILE_MISSING",
                "Artifact file does not exist on disk.",
                404,
                retryable=False,
                details={"artifact_id": artifact.artifact_id, "path": artifact.path},
            )
        return resolved

    def _artifact_root(self) -> Path:
        configured = Path(self.settings.artifact_root)
        root = configured if configured.is_absolute() else (self.settings.root_dir / configured)
        resolved = root.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    @staticmethod
    def _sha256_for_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                digest.update(chunk)
        return digest.hexdigest()
