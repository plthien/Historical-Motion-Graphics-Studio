from __future__ import annotations

from app.core.config import get_settings
from app.db.session import build_session_factory
from app.modules.platform.external_script_analysis import ExternalScriptAnalysisService
from app.modules.v4.voice_spoken_text import compile_voice_spoken_text
from tests.support.external_authoring import (
    combined_script_analysis_output,
    create_channel_project,
    external_import_payload,
    import_and_select_topic,
)


def _request() -> dict:
    return {
        "user_input": {
            "target_duration_minutes": 1,
            "pov_style": "evidence-led explanatory narration",
            "avoid": [],
            "section_count_mode": "llm_decides",
            "pacing_mode": "medium",
            "narration_wpm": {"min": 145, "max": 165},
        },
        "selected_topic_idea_id": "idea_001",
    }


def _dmanisi_output() -> dict:
    output = combined_script_analysis_output()
    section = output["script"]["script_sections"][0]
    section["narration"] = (
        "The cranium is D3444. Its associated mandible is D3900. "
        "The fossil context dates to about 1.77 million years ago. "
        "That evidence changes the question."
    )
    section["spoken_overrides"] = [
        {
            "anchor_quote": "D3444",
            "context_quote": "The cranium is D3444.",
            "spoken_text": "D three four four four",
        },
        {
            "anchor_quote": "D3900",
            "context_quote": "Its associated mandible is D3900.",
            "spoken_text": "D three nine zero zero",
        },
        {
            "anchor_quote": "1.77",
            "context_quote": "The fossil context dates to about 1.77 million years ago.",
            "spoken_text": "one point seven seven",
        },
    ]
    section["delivery_cues"] = [
        {
            "anchor_quote": "That evidence changes the question.",
            "context_quote": "That evidence changes the question.",
            "kind": "pause_after",
            "pause_strength": "medium",
        }
    ]
    return output


def test_realistic_authoring_preserves_reader_facing_identifiers_and_spoken_mapping() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        result = service.import_result(
            project_id,
            external_import_payload(package, _dmanisi_output()),
        )

        script = result["script"]
        section = script["script_sections"][0]
        authored = section["narration"]
        assert "D3444" in authored
        assert "D3900" in authored
        assert "1.77 million years ago" in authored
        assert "D three four four four" not in authored
        assert "one point seven seven" not in authored

        assert [item["anchor_quote"] for item in section["spoken_overrides"]] == [
            "D3444",
            "D3900",
            "1.77",
        ]

        compiled = compile_voice_spoken_text(script).sections[0]
        canonical_spoken = compiled["canonical_spoken_text"]
        provider_text = compiled["provider_text"]

        assert "D3444" not in canonical_spoken
        assert "D3900" not in canonical_spoken
        assert "1.77" not in canonical_spoken
        assert "D three four four four" in canonical_spoken
        assert "D three nine zero zero" in canonical_spoken
        assert "one point seven seven million years ago" in canonical_spoken
        assert "<" not in canonical_spoken
        assert ">" not in canonical_spoken
        assert provider_text.count("<break") == 1

        authored_spans = [
            script["full_script"][item["source_start"]:item["source_end"]]
            for item in section["spoken_overrides"]
        ]
        assert authored_spans == ["D3444", "D3900", "1.77"]


def test_sparse_delivery_remains_legitimately_empty_after_authoring_audit() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = _dmanisi_output()
        output["script"]["script_sections"][0]["delivery_cues"] = []
        result = service.import_result(
            project_id,
            external_import_payload(package, output),
        )
        compiled = compile_voice_spoken_text(result["script"]).sections[0]
        assert "<break" not in compiled["provider_text"]
