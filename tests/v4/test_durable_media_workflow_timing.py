from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.modules.media.durable_workflow.timing import (
    minus_seconds,
    parse_iso,
    plus_seconds,
    stale_before_iso,
    submit_stale_at,
)


def test_shared_timing_owns_offsets_and_submit_stale_boundary() -> None:
    base = "2026-08-18T05:00:00+00:00"
    assert plus_seconds(base, 30) == "2026-08-18T05:00:30+00:00"
    assert minus_seconds(base, 30) == "2026-08-18T04:59:30+00:00"
    assert submit_stale_at(base, stale_after_seconds=30) == datetime(
        2026, 8, 18, 5, 0, 30, tzinfo=UTC
    )
    assert stale_before_iso(
        now=datetime(2026, 8, 18, 5, 0, 30, tzinfo=UTC),
        seconds=30,
    ) == base
    assert parse_iso("2026-08-18T12:00:00+07:00") == datetime(
        2026, 8, 18, 5, 0, 0, tzinfo=UTC
    )


def test_domain_runners_do_not_reimplement_iso_or_stale_deadline_arithmetic() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "app/scene_asset_coordinator_runner.py",
        "app/visual_reference_coordinator_runner.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "def _parse_iso" not in source
        assert "datetime.fromisoformat" not in source
        assert "timedelta(seconds=self.stale_submit_seconds)" not in source
        assert "submit_stale_at(" in source
