from __future__ import annotations

from types import SimpleNamespace

from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService


class _Session:
    def __init__(self) -> None:
        self.scalar_calls = 0
        self.revision = SimpleNamespace(
            prompt_revision_id="revision_001",
            project_id="project_001",
            beat_id="beat_001",
            is_current=True,
        )

    def scalar(self, _statement):
        self.scalar_calls += 1
        return self.revision


def test_current_prompt_revision_reuses_service_cache() -> None:
    session = _Session()
    service = SceneAssetPromptRevisionService(session)

    first = service.current("project_001", "beat_001")
    second = service.current("project_001", "beat_001")

    assert first is second
    assert session.scalar_calls == 1


def test_non_current_cached_revision_is_reloaded() -> None:
    session = _Session()
    service = SceneAssetPromptRevisionService(session)

    first = service.current("project_001", "beat_001")
    first.is_current = False
    replacement = SimpleNamespace(
        prompt_revision_id="revision_002",
        project_id="project_001",
        beat_id="beat_001",
        is_current=True,
    )
    session.revision = replacement

    assert service.current("project_001", "beat_001") is replacement
    assert session.scalar_calls == 2
