from __future__ import annotations

from types import SimpleNamespace

from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService


class _Session:
    def __init__(self) -> None:
        self.scalars_calls = 0
        self.scalar_calls = 0
        self.revisions = [
            SimpleNamespace(
                prompt_revision_id="revision_001",
                project_id="project_001",
                beat_id="beat_001",
                is_current=True,
            ),
            SimpleNamespace(
                prompt_revision_id="revision_002",
                project_id="project_001",
                beat_id="beat_002",
                is_current=True,
            ),
        ]

    def scalars(self, _statement):
        self.scalars_calls += 1
        return list(self.revisions)

    def scalar(self, _statement):
        self.scalar_calls += 1
        raise AssertionError("primed prompt revisions must not fall back to per-beat SELECT")


def test_prime_current_populates_all_requested_prompt_revision_cache_entries() -> None:
    session = _Session()
    service = SceneAssetPromptRevisionService(session)

    revisions = service.prime_current("project_001", ["beat_002", "beat_001", "beat_001"])

    assert len(revisions) == 2
    assert service.current("project_001", "beat_001").prompt_revision_id == "revision_001"
    assert service.current("project_001", "beat_002").prompt_revision_id == "revision_002"
    assert session.scalars_calls == 1
    assert session.scalar_calls == 0
