from __future__ import annotations

import re
from uuid import uuid4

from app.core.config import get_settings
from app.db.enums import ArtifactType
from app.db.session import get_session_factory
from app.services.artifacts import ArtifactService
from app.repositories import ArtifactRepository


def test_local_artifact_revision_directory_is_compact_and_does_not_repeat_public_id() -> None:
    factory = get_session_factory()
    logical_id = f"test/artifact-path/{uuid4().hex}"

    with factory() as session:
        staged = ArtifactService(session, ArtifactRepository(session), get_settings()).write_artifact(
            logical_id=logical_id,
            artifact_type=ArtifactType.JSON,
            filename="payload.json",
            content=b'{"ok":true}',
        )
        try:
            relative_path = staged.artifact.path
            parts = relative_path.split("/")
            revision_dir = parts[-2]

            assert staged.artifact_id.startswith("artifact_")
            assert staged.artifact_id not in relative_path
            assert re.fullmatch(r"r-[0-9a-f]{12}", revision_dir)
            assert len(revision_dir) == 14
        finally:
            session.rollback()
            staged.cleanup()


def test_compact_revision_directory_resolves_through_database_path_mapping() -> None:
    factory = get_session_factory()
    logical_id = f"test/artifact-path-resolve/{uuid4().hex}"

    with factory() as session:
        service = ArtifactService(session, ArtifactRepository(session), get_settings())
        staged = service.write_artifact(
            logical_id=logical_id,
            artifact_type=ArtifactType.JSON,
            filename="payload.json",
            content=b'{"value":1}',
        )
        try:
            resolved = service.resolve_file_path(staged.artifact)
            assert resolved == staged.filepath
            assert resolved.read_bytes() == b'{"value":1}'
            assert resolved.is_relative_to(
                get_settings().resolve_configured_path(get_settings().artifact_root)
            )
        finally:
            session.rollback()
            staged.cleanup()
