from app.modules.media.media_task_service import effective_media_poll_interval_seconds


def test_effective_media_poll_interval_matches_configured_cadence():
    assert effective_media_poll_interval_seconds(1) == 1
    assert effective_media_poll_interval_seconds(4) == 4
    assert effective_media_poll_interval_seconds(5) == 5
    assert effective_media_poll_interval_seconds(30) == 30
