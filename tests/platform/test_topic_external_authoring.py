from __future__ import annotations

import json

import pytest

from app.api.errors import AppError
from app.db.session import build_session_factory
from app.core.config import get_settings
from app.modules.platform.services import AuthoringService
from tests.support.external_authoring import (
    create_channel_project,
    create_project_in_channel,
    external_import_payload,
    import_and_select_topic,
    raw_topic_output,
)


def test_topic_prompt_receives_project_channel_pacing_profiles_and_request_input() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        service = AuthoringService(session)
        project = service.get_project_required(project_id)
        project.channel_dna_json["pacing_profiles"]["fast"]["high"]["preferred_hold_seconds"] = {"min": 4.0, "max": 5.0}
        session.commit()
        package = service.build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "memory", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        assert '"narration_identity"' in package["prompt_text"]
        assert '"pacing_profiles"' in package["prompt_text"]
        assert '"min": 4.0' in package["prompt_text"]
        assert '"max": 5.0' in package["prompt_text"]
        assert '"seed_topic": "memory"' in package["prompt_text"]


def test_topic_prompt_fills_existing_avoid_topics_from_selected_topics_only() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        channel_id, first_project_id = create_channel_project(session, project_name="First")
        import_and_select_topic(session, first_project_id, title="The First Human Who Tasted Chili")
        second_project_id = create_project_in_channel(session, channel_id, name="Second")
        # This idea is imported but deliberately not selected and therefore must not be excluded.
        service = AuthoringService(session)
        unselected_package = service.build_topic_ideas_prompt(
            second_project_id,
            {"seed_topic": "ancient food", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        service.import_topic_ideas(
            second_project_id,
            external_import_payload(unselected_package, raw_topic_output("Why Ancient Humans Stored Salt")),
        )
        third_project_id = create_project_in_channel(session, channel_id, name="Third")
        package = service.build_topic_ideas_prompt(
            third_project_id,
            {"seed_topic": "ancient food", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        assert "The First Human Who Tasted Chili" in package["prompt_text"]
        assert "Why Ancient Humans Stored Salt" not in package["prompt_text"]
        assert '"avoid_topics": [' in package["prompt_text"]


def test_topic_import_validates_and_assigns_backend_ids() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        service = AuthoringService(session)
        package = service.build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "memory", "topic_count": 2, "focus_keywords": ["ancient"], "focus": "most_unique"},
        )
        assert package["prompt_version"] == "topic_ideas_external_v6"
        payload = external_import_payload(package, raw_topic_output("Topic A", "Topic B"))
        validation = service.validate_topic_ideas_import(project_id, payload)
        assert validation["valid"] is True
        artifact, artifact_id = service.import_topic_ideas(project_id, payload)
        assert artifact_id.startswith("artifact_")
        assert [idea["idea_id"] for idea in artifact["ideas"]] == ["idea_001", "idea_002"]
        assert artifact["source_user_input"]["avoid_topics"] == []


def test_topic_import_rejects_unknown_reserved_artifact_id_without_publishing() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        service = AuthoringService(session)
        package = service.build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "memory", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        payload = external_import_payload(package, raw_topic_output("Topic A"))
        payload["result_json"] = payload["result_json"].replace(
            package["artifact_ids"]["topic_ideas"], "artifact_ffffffffffff"
        )
        with pytest.raises(AppError) as exc_info:
            service.import_topic_ideas(project_id, payload)
        assert exc_info.value.error_code == "AUTHORING_ARTIFACT_ID_UNKNOWN"
        assert service.get_topic_ideas(project_id)["generated"] is False


def test_topic_import_preserves_script_recommendations() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        service = AuthoringService(session)
        package = service.build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "memory", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        raw = raw_topic_output("Topic A")
        raw["ideas"][0]["pov_style"] = "first-person investigative narration"
        raw["ideas"][0]["avoid"] = ["false certainty", "generic self-help advice"]
        artifact, _artifact_id = service.import_topic_ideas(
            project_id, external_import_payload(package, raw)
        )
        assert artifact["ideas"][0]["pov_style"] == "first-person investigative narration"
        assert artifact["ideas"][0]["avoid"] == ["false certainty", "generic self-help advice"]


def test_topic_prompt_remains_valid_when_another_project_selects_a_new_topic() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        channel_id, first_project_id = create_channel_project(session, project_name="First")
        import_and_select_topic(session, first_project_id, title="Already Selected")
        target_project_id = create_project_in_channel(session, channel_id, name="Target")
        service = AuthoringService(session)
        package = service.build_topic_ideas_prompt(
            target_project_id,
            {"seed_topic": "memory", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        other_project_id = create_project_in_channel(session, channel_id, name="Other")
        import_and_select_topic(session, other_project_id, title="Selected After Prompt Build")

        validation = service.validate_topic_ideas_import(
            target_project_id,
            external_import_payload(package, raw_topic_output("Fresh Candidate")),
        )
        assert validation["valid"] is True


def test_building_a_new_prompt_replaces_the_pending_topic_reservation() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        service = AuthoringService(session)
        old_package = service.build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "memory", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        new_package = service.build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "replacement", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        with pytest.raises(AppError) as exc_info:
            service.validate_topic_ideas_import(
                project_id, external_import_payload(old_package, raw_topic_output("Old Result"))
            )
        assert exc_info.value.error_code == "AUTHORING_ARTIFACT_ID_UNKNOWN"

        artifact, artifact_id = service.import_topic_ideas(
            project_id, external_import_payload(new_package, raw_topic_output("New Result"))
        )
        assert artifact_id == new_package["artifact_ids"]["topic_ideas"]
        assert artifact["ideas"][0]["title"] == "New Result"



def test_topic_json_can_be_uploaded_after_browser_reload_without_prompt_state() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        package = AuthoringService(session).build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "memory", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        payload = external_import_payload(package, raw_topic_output("Reload-safe Topic"))

    # A fresh service/session represents a reloaded browser and a later upload.
    with factory() as session:
        service = AuthoringService(session)
        assert service.validate_topic_ideas_import(project_id, payload)["valid"] is True
        artifact, artifact_id = service.import_topic_ideas(project_id, payload)
        assert artifact_id == package["artifact_ids"]["topic_ideas"]
        assert artifact["ideas"][0]["title"] == "Reload-safe Topic"


def test_consumed_topic_artifact_id_cannot_be_imported_twice() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        service = AuthoringService(session)
        package = service.build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "memory", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )
        payload = external_import_payload(package, raw_topic_output("One-time Topic"))
        service.import_topic_ideas(project_id, payload)
        with pytest.raises(AppError) as exc_info:
            service.import_topic_ideas(project_id, payload)
        assert exc_info.value.error_code == "AUTHORING_ARTIFACT_ALREADY_CONSUMED"


def test_pending_topic_prompt_package_is_restored_after_reload() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        package = AuthoringService(session).build_topic_ideas_prompt(
            project_id,
            {"seed_topic": "memory", "topic_count": 1, "focus_keywords": [], "focus": "balanced"},
        )

    with factory() as session:
        restored = AuthoringService(session).get_pending_topic_ideas_prompt(project_id)
        assert restored["generated"] is True
        assert restored["prompt_package"] == package
        assert restored["prompt_package"]["artifact_ids"] == package["artifact_ids"]
        assert restored["request_input"]["seed_topic"] == "memory"
