from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Project
from app.db.session import create_sqlalchemy_engine
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.v4.visual_reference_workspace_v2 import VisualReferenceWorkspaceV2Service

NOW = "2026-08-18T05:00:00+00:00"


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(
        f"sqlite:///{(tmp_path / 'visual_reference_projection.sqlite3').resolve()}"
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact(artifact_id: str, *, image: bool = False) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"logical/{artifact_id}",
        version_no=1,
        is_current=True,
        superseded_by_artifact_id=None,
        artifact_type=ArtifactType.IMAGE if image else ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.{'png' if image else 'json'}",
        content_hash=f"hash_{artifact_id}",
        sha256=("b" * 64 if image else f"sha_{artifact_id}"),
        mime_type="image/png" if image else "application/json",
        size_bytes=1,
        width=1 if image else None,
        height=1 if image else None,
        duration_sec=None,
        provider_result_url=None,
        provider_source="test",
        source_job_id=None,
        status=ArtifactStatus.READY,
        created_at=NOW,
        deleted_at=None,
    )


def _inventory(count: int) -> list[dict[str, str]]:
    return [
        {
            "anchor_type": "character",
            "anchor_id": f"char_{index:03d}",
            "label": f"Character {index}",
            "source_definition_hash": f"definition_{index}",
            "prompt": f"Prompt {index}",
            "negative_prompt": "No text",
        }
        for index in range(1, count + 1)
    ]


def _seed(factory: sessionmaker[Session], *, anchor_count: int) -> None:
    inventory = _inventory(anchor_count)
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
        session.add_all([_artifact("artifact_style"), _artifact("artifact_image", image=True)])
        session.flush()
        session.add(
            Project(
                project_id="project_vr",
                channel_id="channel_vr",
                name="Visual Reference Projection",
                description=None,
                channel_context_json={},
                channel_dna_json={},
                style_bible_artifact_id="artifact_style",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        for index, item in enumerate(inventory, start=1):
            prompt_id = f"vr_prompt_{index}"
            candidate_id = f"vr_candidate_{index}"
            session.add(
                VisualReferencePromptRevision(
                    prompt_revision_id=prompt_id,
                    project_id="project_vr",
                    anchor_type=item["anchor_type"],
                    anchor_id=item["anchor_id"],
                    revision_no=1,
                    revision_kind="compiled",
                    is_current=1,
                    source_style_bible_artifact_id="artifact_style",
                    source_definition_hash=item["source_definition_hash"],
                    prompt=item["prompt"],
                    negative_prompt=item["negative_prompt"],
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                VisualReferenceCandidate(
                    candidate_id=candidate_id,
                    project_id="project_vr",
                    anchor_type=item["anchor_type"],
                    anchor_id=item["anchor_id"],
                    candidate_index=1,
                    source_prompt_revision_id=prompt_id,
                    source_operation_task_id=None,
                    media_artifact_id="artifact_image",
                    media_task_id=None,
                    mime_type="image/png",
                    media_sha256="b" * 64,
                    provenance_json={
                        "source_kind": "generated",
                        "source_definition_hash": item["source_definition_hash"],
                        "source_style_bible_artifact_id": "artifact_style",
                    },
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                VisualReferenceAnchorState(
                    project_id="project_vr",
                    anchor_type=item["anchor_type"],
                    anchor_id=item["anchor_id"],
                    version=1,
                    resolution_status="approved",
                    review_status="approved",
                    current_prompt_revision_id=prompt_id,
                    selected_candidate_id=candidate_id,
                    approved_candidate_id=candidate_id,
                    active_operation_anchor_id=None,
                    last_error_json=None,
                    last_progress_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        session.commit()


def _project_stub():
    return SimpleNamespace(
        project_id="project_vr",
        style_bible_artifact_id="artifact_style",
        style_bible_visual_reference_set_artifact_id=None,
        style_bible_visual_reference_approval_set_artifact_id=None,
    )


def _count_queries(session: Session, callback) -> tuple[int, object]:
    engine = session.get_bind()
    count = 0

    def before_cursor_execute(*_args):
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        result = callback()
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    return count, result


def test_workspace_query_count_is_constant_with_anchor_count(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, anchor_count=20)
    with factory() as session:
        service = VisualReferenceWorkspaceV2Service(session)
        service._project_inventory = lambda project_id: (_project_stub(), _inventory(20))  # type: ignore[method-assign]
        query_count, result = _count_queries(
            session,
            lambda: service.workspace("project_vr"),
        )
        assert query_count == 4
        assert len(result["anchors"]) == 20
        assert result["readiness"]["approved_count"] == 20
        assert result["readiness"]["ready"] is True


def test_exact_anchor_projection_does_not_build_whole_project_state(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, anchor_count=20)
    with factory() as session:
        service = VisualReferenceWorkspaceV2Service(session)
        service._project_inventory = lambda project_id: (_project_stub(), _inventory(20))  # type: ignore[method-assign]
        query_count, result = _count_queries(
            session,
            lambda: service.anchor("project_vr", "character", "char_010"),
        )
        assert query_count == 3
        assert result["anchor_id"] == "char_010"
        assert len(result["candidates"]) == 1


def test_execution_projection_uses_three_batch_reads_for_operation_unit_tasks(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, anchor_count=1)
    with factory() as session:
        operation = VisualReferenceWorkingOperation(
            operation_id="vr_operation_projection",
            project_id="project_vr",
            operation_type="generate_anchor",
            status="running",
            source_style_bible_artifact_id="artifact_style",
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
        session.add(operation)
        session.flush()
        unit = VisualReferenceOperationAnchor(
            operation_anchor_id="vr_anchor_projection",
            operation_id=operation.operation_id,
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            version=1,
            status="running",
            candidate_count=2,
            prompt_revision_id="vr_prompt_1",
            prompt="Prompt 1",
            negative_prompt="No text",
            generation_options_json={"aspect_ratio": "1:1"},
            generation_options_hash="options",
            source_definition_hash="definition_1",
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        )
        session.add(unit)
        session.flush()
        for index in (1, 2):
            session.add(
                VisualReferenceOperationTask(
                    operation_task_id=f"vr_task_projection_{index}",
                    operation_anchor_id=unit.operation_anchor_id,
                    candidate_count=2,
                    candidate_index=index,
                    status="waiting_provider",
                    attempt_no=1,
                    media_task_id=None,
                    submit_attempt_token=f"submit_{index}",
                    submit_started_at=NOW,
                    next_reconcile_at=NOW,
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
        service = VisualReferenceWorkspaceV2Service(session)
        service._project_required = lambda project_id: _project_stub()  # type: ignore[method-assign]
        query_count, result = _count_queries(
            session,
            lambda: service.executions("project_vr"),
        )
        assert query_count == 3
        assert len(result["executions"]) == 1
        assert len(result["executions"][0]["anchors"][0]["tasks"]) == 2


def test_workspace_and_exact_anchor_reads_do_not_mutate_anchor_version(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, anchor_count=1)
    with factory() as session:
        service = VisualReferenceWorkspaceV2Service(session)
        service._project_inventory = lambda project_id: (_project_stub(), _inventory(1))  # type: ignore[method-assign]
        before = session.get(VisualReferenceAnchorState, ("project_vr", "character", "char_001"))
        assert before is not None
        version = int(before.version)
        service.workspace("project_vr")
        service.anchor("project_vr", "character", "char_001")
        session.expire_all()
        after = session.get(VisualReferenceAnchorState, ("project_vr", "character", "char_001"))
        assert after is not None
        assert int(after.version) == version
