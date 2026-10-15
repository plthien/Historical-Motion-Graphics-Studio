from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.core.ids import generate_id
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, MediaBatch, MediaTask, Project
from app.db.session import create_sqlalchemy_engine
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.media.visual_reference_v2_recovery import recover_visual_reference_v2_media_namespace
from app.modules.v4.visual_reference_loader import VisualReferenceArtifactLoaderV4
from app.modules.v4.visual_reference_publication_v2 import VisualReferencePublicationV2Service
from app.repositories import ProjectRepository, StyleBibleScriptAnalysisCommitGuard

NOW = "2026-08-18T06:00:00+00:00"
PROJECT_ID = "project_aaaaaaaaaaaa"
SCRIPT_ID = "artifact_111111111111"
ANALYSIS_ID = "artifact_222222222222"
STYLE_ID = "artifact_333333333333"
STYLE_ID_2 = "artifact_333333333334"
MEDIA_ID = "artifact_444444444444"
SCENE_ID = "artifact_555555555555"
VISUAL_ID = "artifact_666666666666"
PROMPT_ID = "artifact_777777777777"
MANIFEST_ID = "artifact_888888888888"
DEFINITION_HASH = "sha256:" + ("d" * 64)
MEDIA_BYTES = b"visual-reference-publication-test-media"
MEDIA_HASH = hashlib.sha256(MEDIA_BYTES).hexdigest()
BAD_MEDIA_TASK_ID = "media_task_bbbbbbbbbbbb"
GENERATED_MEDIA_TASK_ID = "media_task_cccccccccccc"
RECOVERY_TASK_ID = "media_task_aaaaaaaaaaaa"
RECOVERY_ANCHOR_ID = "visual_reference_operation_anchor_recoverable"
RECOVERY_OPERATION_ID = "visual_reference_operation_recoverable"
RECOVERY_OPERATION_TASK_ID = "visual_reference_operation_task_recoverable"
RECOVERY_PROMPT_ID = "visual_reference_prompt_revision_recoverable"
RECOVERY_OPTIONS_HASH = "sha256:" + ("e" * 64)
RECOVERY_PROVIDER_HASH = "sha256:" + ("f" * 64)


class _RecoverySettings:
    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = str(artifact_root)

    def resolve_configured_path(self, value: Any) -> Path:
        return Path(value).resolve()


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(
        f"sqlite:///{(tmp_path / 'visual_reference_publication.sqlite3').resolve()}"
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact(
    artifact_id: str,
    *,
    artifact_type: ArtifactType = ArtifactType.JSON,
    logical_id: str | None = None,
    sha256: str | None = None,
    mime_type: str | None = None,
) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=logical_id or f"project/{PROJECT_ID}/seed/{artifact_id}",
        version_no=1,
        is_current=True,
        superseded_by_artifact_id=None,
        artifact_type=artifact_type,
        artifact_role=ArtifactRole.METADATA_FILE if artifact_type == ArtifactType.JSON else None,
        path=f"tests/{artifact_id}.{'png' if artifact_type == ArtifactType.IMAGE else 'json'}",
        content_hash=sha256 or f"hash-{artifact_id}",
        sha256=sha256 or f"sha-{artifact_id}",
        mime_type=mime_type or ("image/png" if artifact_type == ArtifactType.IMAGE else "application/json"),
        size_bytes=len(MEDIA_BYTES) if artifact_type == ArtifactType.IMAGE else 1,
        width=16 if artifact_type == ArtifactType.IMAGE else None,
        height=16 if artifact_type == ArtifactType.IMAGE else None,
        duration_sec=None,
        provider_result_url=None,
        provider_source="test",
        source_job_id=None,
        status=ArtifactStatus.READY,
        created_at=NOW,
        deleted_at=None,
    )


def _inventory() -> list[dict[str, str]]:
    return [
        {
            "anchor_type": "character",
            "anchor_id": "char_001",
            "label": "Primary character",
            "source_definition_hash": DEFINITION_HASH,
            "prompt": "Primitive character",
            "negative_prompt": "No text",
        }
    ]


def _seed(
    factory: sessionmaker[Session],
    *,
    approved: bool = True,
    media_task_id: str | None = None,
    media_logical_id: str | None = None,
    with_downstream: bool = True,
) -> None:
    with factory() as session:
        session.add(
            Channel(
                channel_id="channel_aaaaaaaaaaaa",
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
        seed_artifacts = [
            _artifact(SCRIPT_ID),
            _artifact(ANALYSIS_ID),
            _artifact(STYLE_ID),
            _artifact(
                MEDIA_ID,
                artifact_type=ArtifactType.IMAGE,
                logical_id=media_logical_id or f"project/{PROJECT_ID}/visual_references/media/{MEDIA_ID}",
                sha256=MEDIA_HASH,
                mime_type="image/png",
            ),
        ]
        if with_downstream:
            seed_artifacts.extend(
                [
                    _artifact(SCENE_ID),
                    _artifact(VISUAL_ID),
                    _artifact(PROMPT_ID),
                    _artifact(MANIFEST_ID),
                ]
            )
        session.add_all(seed_artifacts)
        session.flush()
        session.add(
            Project(
                project_id=PROJECT_ID,
                channel_id="channel_aaaaaaaaaaaa",
                name="Visual Reference Publication",
                description=None,
                channel_context_json={},
                channel_dna_json={},
                script_artifact_id=SCRIPT_ID,
                script_analysis_artifact_id=ANALYSIS_ID,
                style_bible_artifact_id=STYLE_ID,
                scene_plan_artifact_id=SCENE_ID if with_downstream else None,
                visual_plan_artifact_id=VISUAL_ID if with_downstream else None,
                prompt_bundle_artifact_id=PROMPT_ID if with_downstream else None,
                scene_asset_manifest_artifact_id=MANIFEST_ID if with_downstream else None,
                style_bible_visual_reference_set_artifact_id=None,
                style_bible_visual_reference_approval_set_artifact_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            VisualReferencePromptRevision(
                prompt_revision_id="visual_reference_prompt_revision_aaaaaaaaaaaa",
                project_id=PROJECT_ID,
                anchor_type="character",
                anchor_id="char_001",
                revision_no=1,
                revision_kind="compiled",
                is_current=1,
                source_style_bible_artifact_id=STYLE_ID,
                source_definition_hash=DEFINITION_HASH,
                prompt="Primitive character",
                negative_prompt="No text",
                created_at=NOW,
            )
        )
        session.flush()
        if media_task_id is not None:
            session.add(
                MediaBatch(
                    batch_id="media_batch_generated",
                    purpose="style_bible_visual_reference",
                    project_id=PROJECT_ID,
                    style_bible_revision_id=STYLE_ID,
                    domain_revision_id="visual_reference_operation_generated",
                    total=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add(
                MediaTask(
                    media_task_id=media_task_id,
                    batch_id="media_batch_generated",
                    purpose="style_bible_visual_reference",
                    project_id=PROJECT_ID,
                    style_bible_revision_id=STYLE_ID,
                    domain_revision_id="visual_reference_operation_generated",
                    entity_type="character",
                    entity_id="char_001",
                    candidate_index=1,
                    status="completed",
                    external_provider="glabs_webhook",
                    external_task_id="external-generated",
                    provider_status="completed",
                    provider_submitted_at=NOW,
                    provider_error_code=None,
                    error_code=None,
                    error=None,
                    error_detail=None,
                    prompt="Primitive character",
                    negative_prompt="No text",
                    model="nano_banana_2",
                    aspect_ratio="1:1",
                    upscale_json=[],
                    reference_images_json=[],
                    storage_namespace=(
                        "project/project_aaaaaaaaaaaa/visual_references/"
                        "visual_reference_prompt_revision_aaaaaaaaaaaa/character/char_001"
                    ),
                    metadata_json={"visual_reference_v2": True},
                    result_json=[
                        {
                            "artifact_id": MEDIA_ID,
                            "storage_uri": f"tests/{MEDIA_ID}.png",
                            "mime_type": "image/png",
                            "sha256": MEDIA_HASH,
                        }
                    ],
                    storage_uri=f"tests/{MEDIA_ID}.png",
                    mime_type="image/png",
                    source_result_url=None,
                    selected=False,
                    locked=False,
                    created_at=NOW,
                    updated_at=NOW,
                    completed_at=NOW,
                    poll_count=1,
                    poll_revision=1,
                )
            )
            session.flush()
        candidate = VisualReferenceCandidate(
            candidate_id="visual_reference_candidate_aaaaaaaaaaaa",
            project_id=PROJECT_ID,
            anchor_type="character",
            anchor_id="char_001",
            candidate_index=1,
            source_prompt_revision_id="visual_reference_prompt_revision_aaaaaaaaaaaa",
            source_operation_task_id=None,
            media_artifact_id=MEDIA_ID,
            media_task_id=media_task_id,
            mime_type="image/png",
            media_sha256=MEDIA_HASH,
            provenance_json={
                "source_kind": "user_upload" if media_task_id is None else "generated",
                "source_definition_hash": DEFINITION_HASH,
                "source_style_bible_artifact_id": STYLE_ID,
            },
            created_at=NOW,
        )
        session.add(candidate)
        session.flush()
        session.add(
            VisualReferenceAnchorState(
                project_id=PROJECT_ID,
                anchor_type="character",
                anchor_id="char_001",
                version=1,
                resolution_status="approved" if approved else "candidate_ready",
                review_status="approved" if approved else "draft_selected",
                current_prompt_revision_id="visual_reference_prompt_revision_aaaaaaaaaaaa",
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


def _service(session: Session, tmp_path: Path) -> VisualReferencePublicationV2Service:
    service = VisualReferencePublicationV2Service(session)
    artifact_root = (tmp_path / "artifacts").resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    media_artifact = session.get(Artifact, MEDIA_ID)
    if media_artifact is not None:
        media_path = artifact_root / media_artifact.path
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(MEDIA_BYTES)
    service.artifacts._artifact_root = lambda: artifact_root  # type: ignore[method-assign]
    service._current_inventory = lambda project: _inventory()  # type: ignore[method-assign]
    return service


def _artifact_payload(
    service: VisualReferencePublicationV2Service,
    artifact_id: str,
) -> dict[str, Any]:
    return json.loads(service.artifacts.read_artifact_bytes(artifact_id))


def _replace_style_bible_and_rebuild_working_state(session: Session) -> None:
    session.add(_artifact(STYLE_ID_2))
    session.flush()
    project = session.get(Project, PROJECT_ID)
    assert project is not None
    updated = ProjectRepository(session).compare_and_set_style_bible_v4(
        project_id=PROJECT_ID,
        new_style_bible_artifact_id=STYLE_ID_2,
        guard=StyleBibleScriptAnalysisCommitGuard(
            script_analysis_artifact_id=ANALYSIS_ID,
            script_artifact_id=SCRIPT_ID,
            channel_dna_json={},
            previous_style_bible_artifact_id=STYLE_ID,
        ),
        updated_at=NOW,
    )
    assert updated
    prompt_revision = session.get(
        VisualReferencePromptRevision,
        "visual_reference_prompt_revision_aaaaaaaaaaaa",
    )
    candidate = session.get(
        VisualReferenceCandidate,
        "visual_reference_candidate_aaaaaaaaaaaa",
    )
    assert prompt_revision is not None
    assert candidate is not None
    prompt_revision.source_style_bible_artifact_id = STYLE_ID_2
    candidate.provenance_json = {
        **candidate.provenance_json,
        "source_style_bible_artifact_id": STYLE_ID_2,
    }
    session.commit()
    session.expire_all()


def _seed_recoverable_generated_namespace_bug(factory: sessionmaker[Session], tmp_path: Path) -> tuple[Path, str, str]:
    bad_logical_id = (
        f"project/{PROJECT_ID}/style_bible/{STYLE_ID}/characters/char_001/"
        f"candidates/{RECOVERY_TASK_ID}/result_001"
    )
    bad_path = (
        Path(bad_logical_id).parent
        / f"{Path(bad_logical_id).name}_v001.png"
    ).as_posix()
    expected_logical_id = (
        f"project/{PROJECT_ID}/visual_references/{RECOVERY_PROMPT_ID}/character/char_001/"
        f"{RECOVERY_TASK_ID}/result_001"
    )
    artifact_root = (tmp_path / "artifacts").resolve()
    (artifact_root / bad_path).parent.mkdir(parents=True, exist_ok=True)
    (artifact_root / bad_path).write_bytes(MEDIA_BYTES)
    with factory() as session:
        session.add(
            Channel(
                channel_id="channel_aaaaaaaaaaaa",
                name="VR Recovery",
                language="en",
                niche="test",
                audience="test",
                channel_dna_json={},
                channel_context_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all(
            [
                _artifact(SCRIPT_ID),
                _artifact(ANALYSIS_ID),
                _artifact(STYLE_ID),
                _artifact(
                    MEDIA_ID,
                    artifact_type=ArtifactType.IMAGE,
                    logical_id=bad_logical_id,
                    sha256=MEDIA_HASH,
                    mime_type="image/png",
                ),
            ]
        )
        session.flush()
        media_artifact = session.get(Artifact, MEDIA_ID)
        assert media_artifact is not None
        media_artifact.path = bad_path
        session.flush()
        session.add(
            Project(
                project_id=PROJECT_ID,
                channel_id="channel_aaaaaaaaaaaa",
                name="Visual Reference Recovery",
                description=None,
                channel_context_json={},
                channel_dna_json={},
                script_artifact_id=SCRIPT_ID,
                script_analysis_artifact_id=ANALYSIS_ID,
                style_bible_artifact_id=STYLE_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            VisualReferencePromptRevision(
                prompt_revision_id=RECOVERY_PROMPT_ID,
                project_id=PROJECT_ID,
                anchor_type="character",
                anchor_id="char_001",
                revision_no=1,
                revision_kind="compiled",
                is_current=1,
                source_style_bible_artifact_id=STYLE_ID,
                source_definition_hash=DEFINITION_HASH,
                prompt="Primitive character",
                negative_prompt="No text",
                created_at=NOW,
            )
        )
        session.add(
            VisualReferenceWorkingOperation(
                operation_id=RECOVERY_OPERATION_ID,
                project_id=PROJECT_ID,
                operation_type="generate_anchor",
                status="completed",
                source_style_bible_artifact_id=STYLE_ID,
                expected_visual_reference_set_artifact_id=None,
                provider_settings_snapshot_json={"provider": "test"},
                provider_settings_snapshot_hash=RECOVERY_PROVIDER_HASH,
                total_anchor_count=1,
                completed_count=1,
                failed_count=0,
                cancelled_count=0,
                next_reconcile_at=None,
                last_progress_at=NOW,
                reconcile_owner=None,
                reconcile_token=None,
                reconcile_lease_expires_at=None,
                cancel_requested_at=None,
                cancelled_at=None,
                request_json={},
                result_json={},
                error_code=None,
                error_message=None,
                error_details_json=None,
                created_at=NOW,
                updated_at=NOW,
                completed_at=NOW,
            )
        )
        session.flush()
        session.add(
            VisualReferenceOperationAnchor(
                operation_anchor_id=RECOVERY_ANCHOR_ID,
                operation_id=RECOVERY_OPERATION_ID,
                project_id=PROJECT_ID,
                anchor_type="character",
                anchor_id="char_001",
                version=1,
                status="candidate_ready",
                candidate_count=1,
                prompt_revision_id=RECOVERY_PROMPT_ID,
                prompt="Primitive character",
                negative_prompt="No text",
                generation_options_json={"aspect_ratio": "1:1"},
                generation_options_hash=RECOVERY_OPTIONS_HASH,
                source_definition_hash=DEFINITION_HASH,
                error_code=None,
                error_message=None,
                error_details_json=None,
                created_at=NOW,
                updated_at=NOW,
                completed_at=NOW,
            )
        )
        session.flush()
        session.add(
            MediaBatch(
                batch_id="media_batch_recoverable",
                purpose="style_bible_visual_reference",
                project_id=PROJECT_ID,
                style_bible_revision_id=STYLE_ID,
                domain_revision_id=RECOVERY_OPERATION_ID,
                total=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            MediaTask(
                media_task_id=RECOVERY_TASK_ID,
                batch_id="media_batch_recoverable",
                purpose="style_bible_visual_reference",
                project_id=PROJECT_ID,
                style_bible_revision_id=STYLE_ID,
                domain_revision_id=RECOVERY_OPERATION_ID,
                entity_type="character",
                entity_id="char_001",
                candidate_index=1,
                status="completed",
                external_provider="glabs_webhook",
                external_task_id="external-recoverable",
                provider_status="completed",
                provider_submitted_at=NOW,
                provider_error_code=None,
                error_code=None,
                error=None,
                error_detail=None,
                prompt="Primitive character",
                negative_prompt="No text",
                model="nano_banana_2",
                aspect_ratio="1:1",
                upscale_json=[],
                reference_images_json=[],
                storage_namespace=(
                    f"project/{PROJECT_ID}/visual_references/{RECOVERY_PROMPT_ID}/character/char_001"
                ),
                metadata_json={
                    "visual_reference_v2": True,
                    "visual_reference_operation_id": RECOVERY_OPERATION_ID,
                    "visual_reference_operation_anchor_id": RECOVERY_ANCHOR_ID,
                    "visual_reference_operation_task_id": RECOVERY_OPERATION_TASK_ID,
                    "anchor_type": "character",
                    "anchor_id": "char_001",
                    "candidate_index": 1,
                    "prompt_revision_id": RECOVERY_PROMPT_ID,
                    "source_definition_hash": DEFINITION_HASH,
                    "generation_options_hash": RECOVERY_OPTIONS_HASH,
                    "provider_settings_snapshot_hash": RECOVERY_PROVIDER_HASH,
                    "source_style_bible_artifact_id": STYLE_ID,
                },
                result_json=[
                    {
                        "artifact_id": MEDIA_ID,
                        "storage_uri": bad_path,
                        "mime_type": "image/png",
                        "sha256": MEDIA_HASH,
                    }
                ],
                storage_uri=bad_path,
                mime_type="image/png",
                source_result_url=None,
                selected=False,
                locked=False,
                created_at=NOW,
                updated_at=NOW,
                completed_at=NOW,
                poll_count=1,
                poll_revision=1,
            )
        )
        session.flush()
        session.add(
            VisualReferenceOperationTask(
                operation_task_id=RECOVERY_OPERATION_TASK_ID,
                operation_anchor_id=RECOVERY_ANCHOR_ID,
                candidate_count=1,
                candidate_index=1,
                status="completed",
                attempt_no=1,
                media_task_id=RECOVERY_TASK_ID,
                submit_attempt_token=None,
                submit_started_at=None,
                next_reconcile_at=None,
                error_code=None,
                error_message=None,
                error_details_json=None,
                created_at=NOW,
                updated_at=NOW,
                completed_at=NOW,
            )
        )
        session.flush()
        candidate = VisualReferenceCandidate(
            candidate_id="visual_reference_candidate_recoverable",
            project_id=PROJECT_ID,
            anchor_type="character",
            anchor_id="char_001",
            candidate_index=1,
            source_prompt_revision_id=RECOVERY_PROMPT_ID,
            source_operation_task_id=RECOVERY_OPERATION_TASK_ID,
            media_artifact_id=MEDIA_ID,
            media_task_id=RECOVERY_TASK_ID,
            mime_type="image/png",
            media_sha256=MEDIA_HASH,
            provenance_json={
                "source_kind": "generated",
                "operation_id": RECOVERY_OPERATION_ID,
                "operation_anchor_id": RECOVERY_ANCHOR_ID,
                "operation_task_id": RECOVERY_OPERATION_TASK_ID,
                "provider_settings_snapshot_hash": RECOVERY_PROVIDER_HASH,
                "source_definition_hash": DEFINITION_HASH,
                "source_style_bible_artifact_id": STYLE_ID,
            },
            created_at=NOW,
        )
        session.add(candidate)
        session.flush()
        session.add(
            VisualReferenceAnchorState(
                project_id=PROJECT_ID,
                anchor_type="character",
                anchor_id="char_001",
                version=1,
                resolution_status="approved",
                review_status="approved",
                current_prompt_revision_id=RECOVERY_PROMPT_ID,
                selected_candidate_id=candidate.candidate_id,
                approved_candidate_id=candidate.candidate_id,
                active_operation_anchor_id=None,
                last_error_json=None,
                last_progress_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
    return artifact_root, bad_path, expected_logical_id


def test_publish_materializes_loader_compatible_set_approval_and_reference_artifact(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True, media_task_id=None, with_downstream=True)
    with factory() as session:
        service = _service(session, tmp_path)
        result = service.publish(
            PROJECT_ID,
            expected_visual_reference_set_artifact_id=None,
            expected_approval_set_artifact_id=None,
        )
        assert result["published_anchor_count"] == 1
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        assert project.style_bible_visual_reference_set_artifact_id == result["visual_reference_set_artifact_id"]
        assert project.style_bible_visual_reference_approval_set_artifact_id == result["approval_set_artifact_id"]
        assert project.scene_plan_artifact_id is None
        assert project.visual_plan_artifact_id is None
        assert project.prompt_bundle_artifact_id is None
        assert project.scene_asset_manifest_artifact_id is None

        loader = VisualReferenceArtifactLoaderV4(service.artifacts)
        reference_set = loader.load_set(project, result["visual_reference_set_artifact_id"], require_current=True)
        approval_set = loader.load_approval_set(project, result["approval_set_artifact_id"], require_current=True)
        assert len(reference_set["subjects"]) == 1
        assert len(approval_set["selections"]) == 1
        subject = reference_set["subjects"][0]
        candidate = subject["candidates"][0]
        selection = approval_set["selections"][0]
        assert selection["reference_artifact_id"] == candidate["reference_artifact_id"]
        assert selection["media_artifact_id"] == MEDIA_ID
        assert selection["image_artifact_id"] == MEDIA_ID
        assert candidate["media_sha256"] == MEDIA_HASH
        assert candidate["media_task_id"].startswith("media_task_")
        reference_row = session.get(Artifact, candidate["reference_artifact_id"])
        assert reference_row is not None
        assert reference_row.logical_id == (
            f"project/{PROJECT_ID}/visual_references/candidates/{candidate['reference_artifact_id']}"
        )
        set_row = session.get(Artifact, result["visual_reference_set_artifact_id"])
        approval_row = session.get(Artifact, result["approval_set_artifact_id"])
        assert set_row is not None and set_row.is_current
        assert approval_row is not None and approval_row.is_current
        assert (
            _artifact_payload(service, set_row.artifact_id)[
                "parent_visual_reference_set_artifact_id"
            ]
            is None
        )
        assert (
            _artifact_payload(service, approval_row.artifact_id)[
                "parent_approval_set_artifact_id"
            ]
            is None
        )


def test_same_lineage_republish_supersedes_artifacts_and_keeps_project_parents(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True, with_downstream=False)
    with factory() as session:
        service = _service(session, tmp_path)
        first = service.publish(
            PROJECT_ID,
            expected_visual_reference_set_artifact_id=None,
            expected_approval_set_artifact_id=None,
        )
        second = service.publish(
            PROJECT_ID,
            expected_visual_reference_set_artifact_id=first[
                "visual_reference_set_artifact_id"
            ],
            expected_approval_set_artifact_id=first["approval_set_artifact_id"],
        )

        old_set = session.get(Artifact, first["visual_reference_set_artifact_id"])
        old_approval = session.get(Artifact, first["approval_set_artifact_id"])
        new_set = session.get(Artifact, second["visual_reference_set_artifact_id"])
        new_approval = session.get(Artifact, second["approval_set_artifact_id"])
        assert old_set is not None and not old_set.is_current
        assert old_approval is not None and not old_approval.is_current
        assert new_set is not None and new_set.is_current
        assert new_approval is not None and new_approval.is_current
        assert old_set.superseded_by_artifact_id == new_set.artifact_id
        assert old_approval.superseded_by_artifact_id == new_approval.artifact_id
        assert (
            _artifact_payload(service, new_set.artifact_id)[
                "parent_visual_reference_set_artifact_id"
            ]
            == old_set.artifact_id
        )
        assert (
            _artifact_payload(service, new_approval.artifact_id)[
                "parent_approval_set_artifact_id"
            ]
            == old_approval.artifact_id
        )


def test_style_bible_republication_keeps_artifact_history_but_starts_new_project_lineage(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True, with_downstream=False)
    with factory() as session:
        service = _service(session, tmp_path)
        first = service.publish(
            PROJECT_ID,
            expected_visual_reference_set_artifact_id=None,
            expected_approval_set_artifact_id=None,
        )
        set_a_id = first["visual_reference_set_artifact_id"]
        approval_b_id = first["approval_set_artifact_id"]

        _replace_style_bible_and_rebuild_working_state(session)
        project = session.get(Project, PROJECT_ID)
        set_a = session.get(Artifact, set_a_id)
        approval_b = session.get(Artifact, approval_b_id)
        assert project is not None
        assert project.style_bible_artifact_id == STYLE_ID_2
        assert project.style_bible_visual_reference_set_artifact_id is None
        assert project.style_bible_visual_reference_approval_set_artifact_id is None
        assert set_a is not None and set_a.is_current
        assert approval_b is not None and approval_b.is_current

        second = service.publish(
            PROJECT_ID,
            expected_visual_reference_set_artifact_id=None,
            expected_approval_set_artifact_id=None,
        )
        set_c_id = second["visual_reference_set_artifact_id"]
        approval_d_id = second["approval_set_artifact_id"]
        project = session.get(Project, PROJECT_ID)
        set_a = session.get(Artifact, set_a_id)
        approval_b = session.get(Artifact, approval_b_id)
        set_c = session.get(Artifact, set_c_id)
        approval_d = session.get(Artifact, approval_d_id)

        assert project is not None
        assert project.style_bible_visual_reference_set_artifact_id == set_c_id
        assert project.style_bible_visual_reference_approval_set_artifact_id == approval_d_id
        assert set_a is not None and not set_a.is_current
        assert approval_b is not None and not approval_b.is_current
        assert set_a.superseded_by_artifact_id == set_c_id
        assert approval_b.superseded_by_artifact_id == approval_d_id
        assert set_c is not None and set_c.is_current
        assert approval_d is not None and approval_d.is_current
        assert (
            _artifact_payload(service, set_c_id)[
                "parent_visual_reference_set_artifact_id"
            ]
            is None
        )
        assert (
            _artifact_payload(service, approval_d_id)[
                "parent_approval_set_artifact_id"
            ]
            is None
        )


@pytest.mark.parametrize(
    "raced_logical_id",
    [
        f"project/{PROJECT_ID}/visual_references",
        f"project/{PROJECT_ID}/visual_reference_approvals",
    ],
)
def test_publish_rejects_artifact_current_change_after_transaction_snapshot(
    tmp_path: Path,
    raced_logical_id: str,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True, with_downstream=False)
    with factory() as session:
        before = int(session.scalar(select(func.count()).select_from(Artifact)) or 0)
        service = _service(session, tmp_path)
        approved_snapshot = service._approved_snapshot

        def race_after_snapshot(
            project: Project,
            inventory: list[dict[str, Any]],
        ) -> Any:
            approved = approved_snapshot(project, inventory)
            session.add(
                _artifact(
                    generate_id("artifact"),
                    logical_id=raced_logical_id,
                )
            )
            session.flush()
            return approved

        service._approved_snapshot = race_after_snapshot  # type: ignore[method-assign]
        with pytest.raises(AppError) as raised:
            service.publish(
                PROJECT_ID,
                expected_visual_reference_set_artifact_id=None,
                expected_approval_set_artifact_id=None,
            )
        assert raised.value.error_code == "VISUAL_REFERENCE_PUBLICATION_CONFLICT"
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        assert project.style_bible_visual_reference_set_artifact_id is None
        assert project.style_bible_visual_reference_approval_set_artifact_id is None
        assert int(session.scalar(select(func.count()).select_from(Artifact)) or 0) == before


def test_publish_materializes_loader_compatible_generated_media(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True, media_task_id=GENERATED_MEDIA_TASK_ID, with_downstream=False)
    with factory() as session:
        service = _service(session, tmp_path)
        result = service.publish(
            PROJECT_ID,
            expected_visual_reference_set_artifact_id=None,
            expected_approval_set_artifact_id=None,
        )
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        loader = VisualReferenceArtifactLoaderV4(service.artifacts)
        reference_set = loader.load_set(project, result["visual_reference_set_artifact_id"], require_current=True)
        approval_set = loader.load_approval_set(project, result["approval_set_artifact_id"], require_current=True)
        candidate = reference_set["subjects"][0]["candidates"][0]
        selection = approval_set["selections"][0]
        assert candidate["media_task_id"] == GENERATED_MEDIA_TASK_ID
        assert selection["media_artifact_id"] == MEDIA_ID


@pytest.mark.parametrize(
    "bad_logical_id",
    [
        f"project/{PROJECT_ID}/style_bible/{STYLE_ID}/characters/char_001/candidates/{BAD_MEDIA_TASK_ID}/result_001",
        f"project/{PROJECT_ID}/scene_assets/prompt_bundle/beat_001/{BAD_MEDIA_TASK_ID}/result_001",
        f"project/project_bbbbbbbbbbbb/visual_references/media/{MEDIA_ID}",
        f"media/media_batch_bad/{BAD_MEDIA_TASK_ID}/result_001",
    ],
)
def test_publish_rejects_non_visual_reference_media_namespaces(
    tmp_path: Path,
    bad_logical_id: str,
) -> None:
    factory = _factory(tmp_path)
    _seed(
        factory,
        approved=True,
        media_task_id=BAD_MEDIA_TASK_ID,
        media_logical_id=bad_logical_id,
        with_downstream=False,
    )
    with factory() as session:
        service = _service(session, tmp_path)
        with pytest.raises(AppError) as raised:
            service.publish(
                PROJECT_ID,
                expected_visual_reference_set_artifact_id=None,
                expected_approval_set_artifact_id=None,
            )
        assert raised.value.error_code == "VISUAL_REFERENCE_PUBLICATION_NOT_READY"
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        assert project.style_bible_visual_reference_set_artifact_id is None
        assert project.style_bible_visual_reference_approval_set_artifact_id is None


def test_recovery_repairs_known_generated_namespace_bug_then_publishes_loader_compatible_set(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    artifact_root, bad_path, expected_logical_id = _seed_recoverable_generated_namespace_bug(factory, tmp_path)
    settings = _RecoverySettings(artifact_root)

    assert recover_visual_reference_v2_media_namespace(factory, settings=settings) == 1

    expected_path = (
        Path(expected_logical_id).parent
        / f"{Path(expected_logical_id).name}_v001.png"
    ).as_posix()
    with factory() as session:
        media_artifact = session.get(Artifact, MEDIA_ID)
        media_task = session.get(MediaTask, RECOVERY_TASK_ID)
        assert media_artifact is not None
        assert media_task is not None
        assert media_artifact.logical_id == expected_logical_id
        assert media_artifact.path == expected_path
        assert media_task.storage_uri == expected_path
        assert media_task.result_json[0]["storage_uri"] == expected_path
        assert (artifact_root / bad_path).is_file()
        assert (artifact_root / expected_path).read_bytes() == MEDIA_BYTES

    assert recover_visual_reference_v2_media_namespace(factory, settings=settings) == 0

    with factory() as session:
        service = _service(session, tmp_path)
        result = service.publish(
            PROJECT_ID,
            expected_visual_reference_set_artifact_id=None,
            expected_approval_set_artifact_id=None,
        )
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        loader = VisualReferenceArtifactLoaderV4(service.artifacts)
        reference_set = loader.load_set(project, result["visual_reference_set_artifact_id"], require_current=True)
        approval_set = loader.load_approval_set(project, result["approval_set_artifact_id"], require_current=True)
        assert reference_set["subjects"][0]["candidates"][0]["media_artifact_id"] == MEDIA_ID
        assert approval_set["selections"][0]["media_artifact_id"] == MEDIA_ID


def test_publish_rejects_active_operation_before_staging_snapshot(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True, with_downstream=False)
    with factory() as session:
        session.add(
            VisualReferenceWorkingOperation(
                operation_id="visual_reference_operation_aaaaaaaaaaaa",
                project_id=PROJECT_ID,
                operation_type="generate_anchor",
                status="running",
                source_style_bible_artifact_id=STYLE_ID,
                expected_visual_reference_set_artifact_id=None,
                provider_settings_snapshot_json={"provider": "test"},
                provider_settings_snapshot_hash="hash",
                total_anchor_count=1,
                completed_count=0,
                failed_count=0,
                cancelled_count=0,
                next_reconcile_at=NOW,
                last_progress_at=NOW,
                reconcile_owner=None,
                reconcile_token=None,
                reconcile_lease_expires_at=None,
                cancel_requested_at=None,
                cancelled_at=None,
                request_json={},
                result_json={},
                error_code=None,
                error_message=None,
                error_details_json=None,
                created_at=NOW,
                updated_at=NOW,
                completed_at=None,
            )
        )
        session.commit()

    with factory() as session:
        before = int(session.scalar(select(func.count()).select_from(Artifact)) or 0)
    with factory() as session:
        service = _service(session, tmp_path)
        with pytest.raises(AppError) as raised:
            service.publish(
                PROJECT_ID,
                expected_visual_reference_set_artifact_id=None,
                expected_approval_set_artifact_id=None,
            )
        assert raised.value.error_code == "VISUAL_REFERENCE_PUBLICATION_ACTIVE_OPERATION"
    with factory() as session:
        after = int(session.scalar(select(func.count()).select_from(Artifact)) or 0)
        assert after == before


def test_publish_rejects_unapproved_anchor(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=False, with_downstream=False)
    with factory() as session:
        service = _service(session, tmp_path)
        with pytest.raises(AppError) as raised:
            service.publish(
                PROJECT_ID,
                expected_visual_reference_set_artifact_id=None,
                expected_approval_set_artifact_id=None,
            )
        assert raised.value.error_code == "VISUAL_REFERENCE_PUBLICATION_NOT_READY"
        assert raised.value.details["missing_anchor_keys"] == ["character:char_001"]


def test_publish_rejects_stale_expected_pointer_without_artifact_side_effects(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, approved=True, with_downstream=False)
    current_set = "artifact_999999999999"
    current_approval = "artifact_aaaaaaaaaaaa"
    with factory() as session:
        session.add_all([_artifact(current_set), _artifact(current_approval)])
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        project.style_bible_visual_reference_set_artifact_id = current_set
        project.style_bible_visual_reference_approval_set_artifact_id = current_approval
        session.commit()
    with factory() as session:
        before = int(session.scalar(select(func.count()).select_from(Artifact)) or 0)
    with factory() as session:
        service = _service(session, tmp_path)
        with pytest.raises(AppError) as raised:
            service.publish(
                PROJECT_ID,
                expected_visual_reference_set_artifact_id=None,
                expected_approval_set_artifact_id=None,
            )
        assert raised.value.error_code == "VISUAL_REFERENCE_PUBLICATION_CONFLICT"
    with factory() as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        assert project.style_bible_visual_reference_set_artifact_id == current_set
        assert project.style_bible_visual_reference_approval_set_artifact_id == current_approval
        assert int(session.scalar(select(func.count()).select_from(Artifact)) or 0) == before
