from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.session import build_session_factory
from app.modules.platform.external_script_analysis import ExternalScriptAnalysisService
from app.modules.platform.services import ChannelService
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService
from tests.support.external_authoring import create_channel_project, import_and_select_topic
from tests.support.fixtures import sample_channel_dna
from tests.v4.combined_scene_breakdown_support import approved_seed, full_external_output


def test_channel_dna_requires_free_form_narration_and_editorial_identity() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ChannelService(session)
        arbitrary = sample_channel_dna()
        arbitrary["narration_identity"] = {
            "voice": ["warm analytical narrator with clipped surreal asides"],
            "delivery": ["quietly energetic but never comedic"],
            "explanation_style": ["explain through spatial metaphors and concrete physical consequences"],
            "tone_boundaries": ["do not become bedtime narration"],
            "avoid": ["sports-commentary cadence", "meme slang"],
        }
        arbitrary["visual_identity"]["editorial_language"] = [
            "jump from lived scene to isolated object whenever that makes the same locked meaning funnier and clearer",
            "use tiny deadpan text punches only when the Beat already owns the meaning",
        ]
        validated, _context = service.validate_channel_dna(arbitrary)
        assert validated["narration_identity"] == arbitrary["narration_identity"]
        assert validated["visual_identity"]["editorial_language"] == arbitrary["visual_identity"]["editorial_language"]

        missing = deepcopy(arbitrary)
        missing.pop("narration_identity")
        with pytest.raises(AppError) as exc_info:
            service.validate_channel_dna(missing)
        assert exc_info.value.error_code == "CHANNEL_DNA_V3_INVALID"

        missing_editorial = deepcopy(arbitrary)
        missing_editorial["visual_identity"].pop("editorial_language")
        with pytest.raises(AppError) as exc_info:
            service.validate_channel_dna(missing_editorial)
        assert exc_info.value.error_code == "CHANNEL_DNA_V3_INVALID"


def test_channel_dna_rejects_reversed_narration_wpm_range() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ChannelService(session)
        invalid = sample_channel_dna()
        invalid["narration_wpm"] = {"min": 220, "max": 180}
        with pytest.raises(AppError) as exc_info:
            service.validate_channel_dna(invalid)
        assert exc_info.value.error_code == "CHANNEL_DNA_V3_INVALID"
        assert exc_info.value.details["errors"][0]["path"] == "$.narration_wpm"


def test_backend_source_does_not_define_closed_narration_style_profiles_or_ratios() -> None:
    app_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app").rglob("*.py")
    ).casefold()
    for forbidden in (
        "entertaining_explainer",
        "serious_documentary",
        "midnight_storyteller",
        "casefile_profile",
        "humor_ratio",
        "factual_ratio",
        "65–70%",
        "30–35%",
        "editorial_meme_mode",
        "representation_mode_enum",
        "text_punch_mode",
    ):
        assert forbidden.casefold() not in app_source


def test_script_prompt_reservation_rejects_project_channel_dna_change_after_build() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(
            project_id,
            {
                "user_input": {
                    "target_duration_minutes": 1,
                    "pov_style": "mixed",
                    "avoid": [],
                    "section_count_mode": "llm_decides",
                    "pacing_mode": "medium",
                    "narration_wpm": {"min": 145, "max": 180},
                },
                "selected_topic_idea_id": "idea_001",
            },
        )
        project = service.projects.get(project_id)
        assert project is not None
        project.channel_dna_json["narration_identity"]["voice"] = ["mutated after prompt build"]
        session.commit()

        from tests.support.external_authoring import combined_script_analysis_output, external_import_payload

        with pytest.raises(AppError) as exc_info:
            service.validate_import(
                project_id,
                external_import_payload(package, combined_script_analysis_output()),
            )
        assert exc_info.value.error_code == "AUTHORING_SOURCE_CHANGED"


def test_scene_prompt_carries_exact_frozen_narration_identity() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        project, script, analysis, style, word_timing = service._current_context(project_id)
        payload, _sections = service._full_input(project, script, analysis, style, word_timing)
        assert payload["narration_context"]["narration_identity"] == project.channel_dna_json["narration_identity"]
        prompt = service.build_prompt(project_id)["prompt_text"]
        assert "Narration delivery is not evidence" in prompt
        assert "narration_context.narration_identity" in prompt
        assert "read-only interpretation context" in prompt
        assert "upstream Script-composition guidance" in prompt
        assert "provenance only" in prompt
        assert "The Script is already frozen" in prompt
        assert "convert stylistic language into factual evidence" in prompt


def test_scene_prompt_reservation_rejects_project_channel_dna_change_after_build() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        project = service.service._project(project_id)
        assert project is not None
        project.channel_dna_json["narration_identity"]["delivery"] = ["mutated after scene prompt build"]
        session.commit()

        from tests.support.external_authoring import external_import_payload

        with pytest.raises(AppError) as exc_info:
            service.validate_import(
                project_id,
                external_import_payload(package, full_external_output()),
            )
        assert exc_info.value.error_code == "STYLE_BIBLE_UPSTREAM_STALE"
