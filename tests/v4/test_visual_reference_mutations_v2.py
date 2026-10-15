from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Project
from app.db.session import create_sqlalchemy_engine
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.modules.v4.visual_reference_mutations_v2 import VisualReferenceMutationsV2

NOW = "2026-08-18T04:00:00+00:00"


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(
        f"sqlite:///{(tmp_path / 'visual_reference_mutations.sqlite3').resolve()}"
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact(artifact_id: str, *, image: bool = False) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=(
            f"project/project_vr/visual_references/seed/{artifact_id}"
            if image
            else f"logical/{artifact_id}"
        ),
        version_no=1,
        is_current=True,
        superseded_by_artifact_id=None,
        artifact_type=ArtifactType.IMAGE if image else ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.{'png' if image else 'json'}",
        content_hash=f"hash_{artifact_id}",
        sha256=("a" * 64 if image else f"sha_{artifact_id}"),
        mime_type="image/png" if image else "application/json",
        size_bytes=1,
        width=1 if image else None,
        height=1 if image else None,
        duration_sec=None,
        provider_result_url=None,
        provider_source="seed",
        source_job_id=None,
        status=ArtifactStatus.READY,
        created_at=NOW,
        deleted_at=None,
    )


def _seed(factory: sessionmaker[Session], *, approved: bool = True) -> None:
    with factory() as session:
        session.add(
            Channel(
                channel_id="channel_vr",
                name="VR",
                language="en",
                niche="test",
                audience="test",
                channel_dna_json={},
                channel_context_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all([_artifact("artifact_style"), _artifact("artifact_seed_image", image=True)])
        session.flush()
        session.add(
            Project(
                project_id="project_vr",
                channel_id="channel_vr",
                name="Visual Reference Mutations",
                description=None,
                channel_context_json={},
                channel_dna_json={},
                style_bible_artifact_id="artifact_style",
                style_bible_visual_reference_set_artifact_id=None,
                style_bible_visual_reference_approval_set_artifact_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            VisualReferencePromptRevision(
                prompt_revision_id="vr_prompt_1",
                project_id="project_vr",
                anchor_type="character",
                anchor_id="char_001",
                revision_no=1,
                revision_kind="compiled",
                is_current=1,
                source_style_bible_artifact_id="artifact_style",
                source_definition_hash="definition_hash",
                prompt="Original prompt",
                negative_prompt="Original negative",
                created_at=NOW,
            )
        )
        session.flush()
        candidate = VisualReferenceCandidate(
            candidate_id="vr_candidate_seed",
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            candidate_index=1,
            source_prompt_revision_id="vr_prompt_1",
            source_operation_task_id=None,
            media_artifact_id="artifact_seed_image",
            media_task_id=None,
            mime_type="image/png",
            media_sha256="a" * 64,
            provenance_json={
                "source_kind": "generated",
                "source_definition_hash": "definition_hash",
                "source_style_bible_artifact_id": "artifact_style",
            },
            created_at=NOW,
        )
        session.add(candidate)
        session.flush()
        session.add(
            VisualReferenceAnchorState(
                project_id="project_vr",
                anchor_type="character",
                anchor_id="char_001",
                version=1,
                resolution_status="approved" if approved else "candidate_ready",
                review_status="approved" if approved else "draft_selected",
                current_prompt_revision_id="vr_prompt_1",
                selected_candidate_id=candidate.candidate_id,
                approved_candidate_id=candidate.candidate_id if approved else None,
                active_operation_anchor_id=None,
                last_error_json=None,
                last_progress_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (128, 64, 32)).save(buffer, format="PNG")
    return buffer.getvalue()


def _service(session: Session, tmp_path: Path) -> VisualReferenceMutationsV2:
    service = VisualReferenceMutationsV2(session)
    artifact_root = (tmp_path / "artifacts").resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    service.artifacts._artifact_root = lambda: artifact_root  # type: ignore[method-assign]
    return service


def test_manual_prompt_edit_preserves_stable_approved_candidate(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True)
    with factory() as session:
        result = _service(session, tmp_path).update_prompt(
            "project_vr",
            "character",
            "char_001",
            expected_version=1,
            prompt="Edited prompt",
            negative_prompt="Edited negative",
        )
        assert result["version"] == 2
        assert result["resolution_status"] == "approved"
        assert result["review_status"] == "approved"
        assert result["approved_candidate_id"] == "vr_candidate_seed"

    with factory() as session:
        revisions = list(
            session.scalars(
                select(VisualReferencePromptRevision).order_by(
                    VisualReferencePromptRevision.revision_no
                )
            )
        )
        assert len(revisions) == 2
        assert revisions[0].is_current == 0
        assert revisions[1].is_current == 1
        assert revisions[1].revision_kind == "manual_edit"
        project = session.get(Project, "project_vr")
        assert project is not None
        assert project.style_bible_visual_reference_set_artifact_id is None
        assert project.style_bible_visual_reference_approval_set_artifact_id is None


def test_upload_selects_new_draft_and_clears_only_working_approval(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True)
    with factory() as session:
        result = _service(session, tmp_path).upload_reference(
            "project_vr",
            "character",
            "char_001",
            expected_version=1,
            content=_png_bytes(),
            content_type="image/png",
        )
        assert result["version"] == 2
        assert result["candidate_id"] != "vr_candidate_seed"
        assert result["resolution_status"] == "candidate_ready"
        assert result["review_status"] == "draft_selected"
        assert result["approved_candidate_id"] is None
        assert result["published_visual_reference_set_artifact_id"] is None

    with factory() as session:
        candidates = list(session.scalars(select(VisualReferenceCandidate)))
        assert len(candidates) == 2
        uploaded = next(item for item in candidates if item.candidate_id != "vr_candidate_seed")
        assert uploaded.source_operation_task_id is None
        assert uploaded.provenance_json["source_kind"] == "user_upload"
        artifact = session.get(Artifact, uploaded.media_artifact_id)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.IMAGE
        assert artifact.provider_source == "user_upload"
        project = session.get(Project, "project_vr")
        assert project is not None
        assert project.style_bible_visual_reference_set_artifact_id is None
        assert project.style_bible_visual_reference_approval_set_artifact_id is None


def test_approve_and_clear_mutate_anchor_state_without_publication_artifacts(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=False)
    with factory() as session:
        before_json_count = session.scalar(
            select(func.count()).select_from(Artifact).where(
                Artifact.artifact_type == ArtifactType.JSON
            )
        )
        result = _service(session, tmp_path).approve(
            "project_vr",
            "character",
            "char_001",
            expected_version=1,
            candidate_id="vr_candidate_seed",
        )
        assert result["version"] == 2
        assert result["approved_candidate_id"] == "vr_candidate_seed"
        assert result["resolution_status"] == "approved"
        assert result["published_visual_reference_set_artifact_id"] is None
        assert result["published_approval_set_artifact_id"] is None

    with factory() as session:
        cleared = _service(session, tmp_path).clear_approval(
            "project_vr",
            "character",
            "char_001",
            expected_version=2,
        )
        assert cleared["version"] == 3
        assert cleared["approved_candidate_id"] is None
        assert cleared["selected_candidate_id"] == "vr_candidate_seed"
        assert cleared["resolution_status"] == "candidate_ready"
        assert cleared["review_status"] == "draft_selected"
        after_json_count = session.scalar(
            select(func.count()).select_from(Artifact).where(
                Artifact.artifact_type == ArtifactType.JSON
            )
        )
        assert after_json_count == before_json_count
        project = session.get(Project, "project_vr")
        assert project is not None
        assert project.style_bible_visual_reference_set_artifact_id is None
        assert project.style_bible_visual_reference_approval_set_artifact_id is None


def test_stale_anchor_version_rejects_mutation_without_side_effects(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=False)
    with factory() as session:
        with pytest.raises(AppError) as raised:
            _service(session, tmp_path).approve(
                "project_vr",
                "character",
                "char_001",
                expected_version=0,
                candidate_id="vr_candidate_seed",
            )
        assert raised.value.error_code == "VISUAL_REFERENCE_V2_ANCHOR_STATE_CONFLICT"
        session.rollback()

    with factory() as session:
        state = session.get(
            VisualReferenceAnchorState,
            ("project_vr", "character", "char_001"),
        )
        assert state is not None
        assert state.version == 1
        assert state.approved_candidate_id is None
