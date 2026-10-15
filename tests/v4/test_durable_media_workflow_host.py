from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.modules.media.durable_workflow.host import (
    DurableMediaWorkflowHost,
    RegisteredDurableWorkflow,
)


@dataclass
class _Runner:
    outcomes: list[object]
    calls: int = 0

    def run_once(self) -> bool:
        self.calls += 1
        if not self.outcomes:
            return False
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return bool(outcome)


def test_host_rotates_first_priority_and_services_all_domains() -> None:
    first = _Runner([True, False])
    second = _Runner([False, True])
    host = DurableMediaWorkflowHost(
        (
            RegisteredDurableWorkflow("scene-assets-v2", first),
            RegisteredDurableWorkflow("visual-references-v2", second),
        )
    )

    assert host.run_once() is True
    assert host.run_once() is True
    assert first.calls == 2
    assert second.calls == 2


def test_host_does_not_starve_other_domain_when_one_iteration_fails() -> None:
    broken = _Runner([RuntimeError("boom")])
    healthy = _Runner([True])
    host = DurableMediaWorkflowHost(
        (
            RegisteredDurableWorkflow("scene-assets-v2", broken),
            RegisteredDurableWorkflow("visual-references-v2", healthy),
        )
    )

    assert host.run_once() is True
    assert broken.calls == 1
    assert healthy.calls == 1


def test_host_rejects_empty_or_duplicate_registrations() -> None:
    runner = _Runner([])
    with pytest.raises(ValueError):
        DurableMediaWorkflowHost(())
    with pytest.raises(ValueError):
        DurableMediaWorkflowHost(
            (
                RegisteredDurableWorkflow("same", runner),
                RegisteredDurableWorkflow("same", runner),
            )
        )


def test_production_host_registers_all_durable_media_domains() -> None:
    from app.durable_media_workflow_runner import build_host

    host = build_host()
    try:
        assert tuple(item.name for item in host.workflows) == (
            "scene-assets-v2",
            "visual-references-v2",
            "voice",
        )
    finally:
        host.close()


def test_windows_launcher_has_exactly_four_runtime_processes() -> None:
    root_launcher = Path("start.bat").read_text(encoding="utf-8")
    durable_launcher = Path("scripts/windows/start_durable_media_workflow.bat").read_text(encoding="utf-8")
    render_launcher = Path("scripts/windows/start_durable_render.bat").read_text(encoding="utf-8")
    service_starts = [
        line.strip()
        for line in root_launcher.splitlines()
        if line.strip().startswith('start "YAS3 ')
    ]

    assert len(service_starts) == 4
    assert any('start "YAS3 Backend API"' in line for line in service_starts)
    assert any('start "YAS3 Durable Media Workflow"' in line for line in service_starts)
    assert any('start "YAS3 Durable Render"' in line for line in service_starts)
    assert any('start "YAS3 Frontend"' in line for line in service_starts)
    assert not any('start "YAS3 Backend Worker"' in line for line in service_starts)
    assert "start_scene_asset_coordinator.bat" not in root_launcher
    assert "visual_reference_coordinator_runner" not in root_launcher
    assert "app.durable_media_workflow_runner" in durable_launcher
    assert "app.durable_render_runner" in render_launcher


def test_domain_runners_are_not_standalone_process_entrypoints() -> None:
    assert not Path("scripts/windows/start_scene_asset_coordinator.bat").exists()
    for path in (
        Path("app/scene_asset_coordinator_runner.py"),
        Path("app/visual_reference_coordinator_runner.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "def main()" not in source
        assert "run_forever(" not in source
        assert 'if __name__ == "__main__"' not in source
