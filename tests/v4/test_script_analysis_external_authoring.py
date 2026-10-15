from __future__ import annotations

from copy import deepcopy

import pytest

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.session import build_session_factory
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.platform.external_script_analysis import ExternalScriptAnalysisService
from app.modules.platform.services import AuthoringService
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.script_analysis_service import ScriptAnalysisService
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
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
            "pov_style": "second person with measured explanatory passages",
            "avoid": [],
            "section_count_mode": "llm_decides",
            "pacing_mode": "medium",
            "narration_wpm": {"min": 145, "max": 165},
        },
        "selected_topic_idea_id": "idea_001",
    }


def test_combined_script_analysis_import_publishes_exact_pair() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        assert package["prompt_version"] == "script_and_analysis_external_v17"
        assert "Plan the complete Script architecture before prose" in package["prompt_text"]
        assert "Analyze only that exact frozen Script" in package["prompt_text"]
        assert "Narration Performance composition/rehearsal pass" in package["prompt_text"]
        assert "Final exact-text Human Performance / Virtual Table Read" in package["prompt_text"]
        assert "last prose-quality gate" in package["prompt_text"]
        assert "Canonical Script narration is **authored-text truth**" in package["prompt_text"]
        assert "do not pre-normalize those tokens out of authored narration" in package["prompt_text"]
        assert "delivery-cue candidates, **not automatic pauses**" in package["prompt_text"]
        assert '"narration_identity"' in package["prompt_text"]
        payload = external_import_payload(package, combined_script_analysis_output())
        validation = service.validate_import(project_id, payload)
        assert validation["valid"] is True
        result = service.import_result(project_id, payload)
        assert result["script_artifact_id"].startswith("artifact_")
        assert result["script_analysis_artifact_id"].startswith("artifact_")
        assert result["script_analysis"]["source_script_revision_id"] == result["script_artifact_id"]
        project = service.projects.get(project_id)
        assert project is not None
        assert project.script_artifact_id == result["script_artifact_id"]
        assert project.script_analysis_artifact_id == result["script_analysis_artifact_id"]


def test_invalid_analysis_quote_does_not_publish_script_or_analysis() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = combined_script_analysis_output()
        output["analysis"]["narrative_markers"] = [
            {
                "marker_type": "reveal",
                "section_id": "sec_001",
                "anchor_quote": "This quote does not exist.",
                "context_quote": "This quote does not exist.",
                "importance": "high",
                "purpose": "Force an invalid quote.",
            }
        ]
        with pytest.raises(AppError):
            service.import_result(project_id, external_import_payload(package, output))
        project = service.projects.get(project_id)
        assert project is not None
        assert project.script_artifact_id is None
        assert project.script_analysis_artifact_id is None


def test_reimport_invalidates_style_bible_and_downstream() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        first = service.import_result(project_id, external_import_payload(package, combined_script_analysis_output()))
        project = service.projects.get(project_id)
        assert project is not None
        project.style_bible_artifact_id = first["script_analysis_artifact_id"]
        project.scene_plan_artifact_id = first["script_artifact_id"]
        project.visual_plan_artifact_id = first["script_analysis_artifact_id"]
        session.commit()
        package2 = service.build_prompt(project_id, _request())
        output2 = combined_script_analysis_output()
        output2["script"]["title"] = "A Revised Script Title"
        second = service.import_result(project_id, external_import_payload(package2, output2))
        assert second["script_artifact_id"] != first["script_artifact_id"]
        project = service.projects.get(project_id)
        assert project is not None
        assert project.style_bible_artifact_id is None
        assert project.scene_plan_artifact_id is None
        assert project.visual_plan_artifact_id is None


def _four_section_output() -> dict:
    output = combined_script_analysis_output()
    sections = output["script"]["script_sections"]
    final = sections.pop()
    words = final["narration"].split()
    split_at = max(1, len(words) // 2)
    sections.extend(
        [
            {
                "section_title": "The First Half of the Resolution",
                "section_role": "development",
                "planned_narration_wpm": final["planned_narration_wpm"],
                "narration": " ".join(words[:split_at]),
                "spoken_overrides": [],
                "delivery_cues": [],
            },
            {
                "section_title": "The Final Resolution",
                "section_role": "resolution",
                "planned_narration_wpm": final["planned_narration_wpm"],
                "narration": " ".join(words[split_at:]),
                "spoken_overrides": [],
                "delivery_cues": [],
            },
        ]
    )
    output["analysis"]["section_guidance"] = [
        {"section_id": f"sec_{index:03d}", "pacing_density": "high" if index == 1 else "medium"}
        for index in range(1, 5)
    ]
    return output


def test_llm_decides_accepts_a_different_section_count_without_word_budgets() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        request = _request()
        package = service.build_prompt(project_id, request)
        result = service.import_result(
            project_id, external_import_payload(package, _four_section_output())
        )
        constraints = result["script"]["computed_constraints"]
        assert result["script"]["source_user_input"]["section_count_mode"] == "llm_decides"
        assert len(result["script"]["script_sections"]) == 4
        assert constraints["section_count_mode"] == "llm_decides"
        assert result["script"]["source_user_input"]["narration_wpm"] == {"min": 145, "max": 165}
        assert "section_budgets" not in constraints
        assert "target_word_count" not in constraints


def test_backend_plan_accepts_a_different_section_count() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        request = _request()
        request["user_input"]["section_count_mode"] = "backend_plan"
        _project, _topic_idea, runtime_input = service._project_and_runtime_input(project_id, request)
        expected_plan = runtime_input["authoring_constraints"]["section_plan"]
        package = service.build_prompt(project_id, request)
        output = _four_section_output()
        for index, plan_item in enumerate(expected_plan):
            if index >= len(output["script"]["script_sections"]):
                break
            output["script"]["script_sections"][index]["section_role"] = plan_item["section_role"]
        result = service.import_result(project_id, external_import_payload(package, output))
        assert len(result["script"]["script_sections"]) == 4
        assert result["script"]["computed_constraints"]["section_count_mode"] == "backend_plan"


def test_script_prompt_defines_pacing_density_by_semantic_visual_change_not_section_role() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        package = ExternalScriptAnalysisService(session).build_prompt(project_id, _request())
        prompt = package["prompt_text"]
        assert "meaningful independently drawable visual contribution" in prompt
        assert "distinct source-owned explanatory/editorial facets" in prompt
        assert "visual refresh opportunity, not narrative/emotional intensity" in prompt
        assert "A quiet, reflective, or evidence-heavy section may still be visually dense" in prompt
        assert "Do not invent numeric beat counts in Script Analysis" in prompt
        assert "Do not infer density from `section_role` alone" in prompt


def test_script_prompt_contains_editable_topic_recommendations_and_file_delivery() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        request = _request()
        request["user_input"]["pov_style"] = "custom editable POV"
        request["user_input"]["avoid"] = ["custom avoid"]
        package = service.build_prompt(project_id, request)
        assert '"pov_style": "custom editable POV"' in package["prompt_text"]
        assert '"avoid": [' in package["prompt_text"]
        assert "script_and_analysis.json" in package["prompt_text"]
        assert "Create and attach/export a UTF-8 JSON file" in package["prompt_text"]


def test_combined_json_can_be_uploaded_after_reload_using_reserved_pair() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        package = ExternalScriptAnalysisService(session).build_prompt(project_id, _request())
        payload = external_import_payload(package, combined_script_analysis_output())

    with factory() as session:
        service = ExternalScriptAnalysisService(session)
        assert service.validate_import(project_id, payload)["valid"] is True
        result = service.import_result(project_id, payload)
        assert result["script_artifact_id"] == package["artifact_ids"]["script"]
        assert result["script_analysis_artifact_id"] == package["artifact_ids"]["analysis"]


@pytest.mark.parametrize(
    ("word", "repeat"),
    [
        ("brief", 3),
        ("overflow", 80),
    ],
)
def test_script_outside_former_word_range_is_accepted_and_reports_runtime_diagnostics(
    word: str,
    repeat: int,
) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = combined_script_analysis_output()
        narration = " ".join([word] * repeat)
        for section in output["script"]["script_sections"]:
            section["narration"] = narration
        payload = external_import_payload(package, output)

        assert service.validate_import(project_id, payload)["valid"] is True
        imported = service.import_result(project_id, payload)
        diagnostics = imported["script"]["runtime_diagnostics"]
        assert diagnostics["actual_word_count"] == imported["script"]["word_count"]
        assert diagnostics["planned_average_wpm"] == imported["script"]["planned_average_wpm"]
        assert diagnostics["target_duration_minutes"] == 1.0
        assert diagnostics["runtime_delta_seconds"] != 0
        project = service.projects.get(project_id)
        assert project is not None
        assert project.script_artifact_id == imported["script_artifact_id"]
        assert project.script_analysis_artifact_id == imported["script_analysis_artifact_id"]


def _ten_minute_runtime_output() -> dict:
    section_roles = [
        "cold_open",
        "first_complication",
        "setup",
        "evidence_and_treatment",
        "character_pressure",
        "midpoint_reversal",
        "adaptation",
        "crisis",
        "climax",
        "resolution",
    ]
    sections = []
    for index, role in enumerate(section_roles, start=1):
        narration = " ".join([f"storyword{chr(96 + index)}"] * 160)
        sections.append(
            {
                "section_title": f"Section {index}",
                "section_role": role,
                "planned_narration_wpm": 160,
                "narration": narration,
                "spoken_overrides": [],
                "delivery_cues": [],
            }
        )
    return {
        "script": {
            "title": "Ten Minute Runtime Script",
            "hook": "A concrete survival problem drives the story.",
            "script_sections": sections,
        },
        "analysis": {
            "section_guidance": [
                {"section_id": f"sec_{index:03d}", "pacing_density": "high"}
                for index in range(1, 11)
            ],
            "narrative_markers": [],
            "continuity_catalog": {
                "characters": [],
                "locations": [],
                "recurring_props": [],
                "recurring_visual_subjects": [],
                "visual_motifs": [],
            },
            "visual_context_catalog": [
                {
                    "label": "Complete authored narrative",
                    "mode": "story_world",
                    "timeframe": None,
                    "setting": None,
                    "constraints": [],
                    "presence_ranges": [
                        {
                            "section_id": f"sec_{index:03d}",
                            "start_quote": section["narration"],
                            "end_quote": section["narration"],
                        }
                        for index, section in enumerate(sections, start=1)
                    ],
                }
            ],
        },
    }


def test_ten_minute_script_reports_runtime_and_builds_style_bible() -> None:
    from app.modules.v4.style_bible_service import StyleBibleServiceV4

    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        script_service = ExternalScriptAnalysisService(session)
        request = _request()
        request["user_input"]["target_duration_minutes"] = 10
        request["user_input"]["narration_wpm"] = {"min": 150, "max": 170}
        package = script_service.build_prompt(project_id, request)
        payload = external_import_payload(package, _ten_minute_runtime_output())

        validation = script_service.validate_import(project_id, payload)
        assert validation["valid"] is True
        assert validation["summary"]["word_count"] == 1600
        imported = script_service.import_result(project_id, payload)
        diagnostics = imported["script"]["runtime_diagnostics"]
        assert diagnostics["target_duration_minutes"] == 10.0
        assert diagnostics["planned_average_wpm"] == 160
        assert diagnostics["estimated_duration_minutes"] == 10.0
        assert diagnostics["runtime_delta_seconds"] == 0

        style, style_id = StyleBibleServiceV4(session).generate(
            project_id,
            imported["script_analysis_artifact_id"],
        )
        assert style_id == style["style_bible_revision_id"]
        assert style["lineage"]["source_script_artifact_id"] == imported["script_artifact_id"]


def _output_with_character_entity_kind(entity_kind: str) -> dict:
    output = combined_script_analysis_output()
    output["analysis"]["continuity_catalog"]["characters"] = [
        {
            "label": "Night watch group",
            "description": "A recurring human identity used to test taxonomy preservation.",
            "explicit_visual_traits": [],
            "identity_markers": ["same recurring identity"],
            "continuity_notes": [],
            "physical_constraints": [],
            "entity_kind": entity_kind,
            "narrative_role": "continuity test subject",
            "occurrences": [
                {
                    "section_id": "sec_001",
                    "quote": "You turn off the light",
                    "context_quote": "You turn off the light, the room goes soft and quiet.",
                },
                {
                    "section_id": "sec_002",
                    "quote": "rest was never just rest",
                    "context_quote": "But for most of human life, rest was never just rest.",
                },
            ],
            "presence_ranges": [
                {
                    "section_id": "sec_001",
                    "start_quote": "You turn off the light, the room goes soft and quiet.",
                    "end_quote": "You turn off the light, the room goes soft and quiet.",
                },
                {
                    "section_id": "sec_002",
                    "start_quote": "But for most of human life, rest was never just rest.",
                    "end_quote": "But for most of human life, rest was never just rest.",
                },
            ],
        }
    ]
    return output


_CONTINUITY_ENTITY_CASES = (
    ("characters", "character_raw", "character"),
    ("locations", "location_raw", "location"),
    ("recurring_props", "prop_raw", "prop"),
    ("recurring_visual_subjects", "recurring_visual_subject_raw", "recurring_visual_subject"),
)


def _long_continuity_description(label: str) -> str:
    description = f"{label} production-bible authority :: " + " | ".join(
        f"{label}-stable-detail-{index:03d}" for index in range(60)
    )
    assert len(description) > 1000
    return description


def _output_with_long_continuity_descriptions() -> tuple[dict, dict[str, str]]:
    output = combined_script_analysis_output()
    descriptions = {
        category: _long_continuity_description(category)
        for category, _raw_def, _canonical_def in _CONTINUITY_ENTITY_CASES
    }
    occurrences = [
        {
            "section_id": "sec_001",
            "quote": "You turn off the light",
            "context_quote": "You turn off the light, the room goes soft and quiet.",
        },
        {
            "section_id": "sec_002",
            "quote": "rest was never just rest",
            "context_quote": "But for most of human life, rest was never just rest.",
        },
    ]
    presence_ranges = [
        {
            "section_id": "sec_001",
            "start_quote": "You turn off the light, the room goes soft and quiet.",
            "end_quote": "You turn off the light, the room goes soft and quiet.",
        },
        {
            "section_id": "sec_002",
            "start_quote": "But for most of human life, rest was never just rest.",
            "end_quote": "But for most of human life, rest was never just rest.",
        },
    ]
    shared = {
        "explicit_visual_traits": ["stable silhouette"],
        "continuity_notes": ["preserve exact recurring identity"],
        "physical_constraints": [],
        "occurrences": occurrences,
        "presence_ranges": presence_ranges,
    }
    catalog = output["analysis"]["continuity_catalog"]
    catalog["characters"] = [
        {
            **deepcopy(shared),
            "label": "Long-description character",
            "description": descriptions["characters"],
            "identity_markers": ["fixed face and clothing"],
            "entity_kind": "human_character",
            "narrative_role": "recurring witness",
        }
    ]
    catalog["locations"] = [
        {
            **deepcopy(shared),
            "label": "Long-description location",
            "description": descriptions["locations"],
            "identity_markers": ["fixed doorway and horizon"],
            "location_kind": "interior shelter",
        }
    ]
    catalog["recurring_props"] = [
        {
            **deepcopy(shared),
            "label": "Long-description prop",
            "description": descriptions["recurring_props"],
            "identity_markers": ["fixed worn handle"],
            "prop_kind": "carried tool",
        }
    ]
    catalog["recurring_visual_subjects"] = [
        {
            **deepcopy(shared),
            "label": "Long-description recurring subject",
            "description": descriptions["recurring_visual_subjects"],
            "visual_type": "comparison evidence system",
        }
    ]
    return output, descriptions


def test_long_continuity_descriptions_survive_external_v2_import_and_read_verbatim() -> None:
    from app.modules.v4.style_bible_service import StyleBibleServiceV4

    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output, descriptions = _output_with_long_continuity_descriptions()
        payload = external_import_payload(package, output)

        assert service.validate_import(project_id, payload)["valid"] is True
        imported = service.import_result(project_id, payload)
        canonical = imported["script_analysis"]
        for category, expected in descriptions.items():
            assert canonical["continuity_catalog"][category][0]["description"] == expected

        V4ContractValidator().validate(
            "script_analysis_v1.schema.json",
            canonical,
            error_code="TEST_SCRIPT_ANALYSIS_INVALID",
        )
        raw = ScriptAnalysisSemanticValidator._raw_projection(canonical)
        V4ContractValidator().validate(
            "script_analysis_llm_output_v1.schema.json",
            raw,
            error_code="TEST_SCRIPT_ANALYSIS_RAW_INVALID",
        )

        stored = ScriptAnalysisService(session).get(project_id)["artifact"]
        for category, expected in descriptions.items():
            assert stored["continuity_catalog"][category][0]["description"] == expected

        style_bible, _style_bible_id = StyleBibleServiceV4(session).generate(
            project_id,
            imported["script_analysis_artifact_id"],
        )
        for category, expected in descriptions.items():
            assert style_bible[category][0]["description"] == expected


@pytest.mark.parametrize(
    ("category", "_raw_def", "_canonical_def"),
    _CONTINUITY_ENTITY_CASES,
)
def test_external_v2_still_rejects_empty_continuity_descriptions(
    category: str,
    _raw_def: str,
    _canonical_def: str,
) -> None:
    output, _descriptions = _output_with_long_continuity_descriptions()
    output["analysis"]["continuity_catalog"][category][0]["description"] = ""

    with pytest.raises(AppError) as exc_info:
        PlatformContractValidator().validate(
            "script_and_analysis_external_output_v2.schema.json",
            output,
            error_code="TEST_EXTERNAL_OUTPUT_INVALID",
        )

    assert {
        "path": f"$.analysis.continuity_catalog.{category}[0].description",
        "keyword": "minLength",
        "message": "'' should be non-empty",
    } in exc_info.value.details["errors"]


def test_only_continuity_description_upper_limits_are_relaxed() -> None:
    v4 = V4ContractValidator()
    schemas_and_defs = (
        (
            v4.load_schema("script_analysis_llm_output_v1.schema.json"),
            tuple(raw_def for _category, raw_def, _canonical_def in _CONTINUITY_ENTITY_CASES),
        ),
        (
            v4.load_schema("script_analysis_v1.schema.json"),
            tuple(canonical_def for _category, _raw_def, canonical_def in _CONTINUITY_ENTITY_CASES),
        ),
        (
            PlatformContractValidator().load_schema(
                "script_and_analysis_external_output_v1.schema.json"
            ),
            tuple(raw_def for _category, raw_def, _canonical_def in _CONTINUITY_ENTITY_CASES),
        ),
        (
            v4.load_schema("project_style_bible_v1.schema.json"),
            ("character", "location", "recurring_prop", "recurring_visual_subject"),
        ),
    )

    for schema, definition_names in schemas_and_defs:
        for definition_name in definition_names:
            properties = schema["$defs"][definition_name]["properties"]
            assert properties["description"] == {"type": "string", "minLength": 1}
            assert properties["label"]["maxLength"] == 160
            assert properties["explicit_visual_traits"]["items"]["maxLength"] == 300
            assert properties["explicit_visual_traits"]["maxItems"] == 12
            assert properties["continuity_notes"]["items"]["maxLength"] == 300
            assert properties["continuity_notes"]["maxItems"] == 10
            assert properties["presence_ranges"]["maxItems"] == 40
            if definition_name not in {
                "recurring_visual_subject_raw",
                "recurring_visual_subject",
            }:
                assert properties["identity_markers"]["items"]["maxLength"] == 300
                assert properties["identity_markers"]["maxItems"] == 12

    update_schema = v4.load_schema("style_bible_text_update_request_v1.schema.json")
    editable_properties = update_schema["properties"]["editable_content"]["properties"]
    for category, _raw_def, _canonical_def in _CONTINUITY_ENTITY_CASES:
        properties = editable_properties[category]["items"]["properties"]
        assert properties["description"] == {"type": "string", "minLength": 1}
        assert properties["label"]["maxLength"] == 160
        assert properties["explicit_visual_traits"]["items"]["maxLength"] == 300
        assert properties["explicit_visual_traits"]["maxItems"] == 12
        assert properties["continuity_notes"]["items"]["maxLength"] == 300
        assert properties["continuity_notes"]["maxItems"] == 10
        if category != "recurring_visual_subjects":
            assert properties["identity_markers"]["items"]["maxLength"] == 300
            assert properties["identity_markers"]["maxItems"] == 12


def test_import_rejects_noncanonical_character_entity_kind_before_publication() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        payload = external_import_payload(package, _output_with_character_entity_kind("group"))

        with pytest.raises(AppError) as exc_info:
            service.validate_import(project_id, payload)
        assert exc_info.value.error_code == "SCRIPT_ANALYSIS_EXTERNAL_OUTPUT_INVALID"
        errors = exc_info.value.details["errors"]
        assert errors == [
            {
                "path": "$.analysis.continuity_catalog.characters[0].entity_kind",
                "keyword": "enum",
                "message": "'group' is not one of ['human_character', 'human_group']",
            }
        ]

        with pytest.raises(AppError) as import_exc:
            service.import_result(project_id, payload)
        assert import_exc.value.error_code == "SCRIPT_ANALYSIS_EXTERNAL_OUTPUT_INVALID"
        project = service.projects.get(project_id)
        assert project is not None
        assert project.script_artifact_id is None
        assert project.script_analysis_artifact_id is None


def test_human_group_import_is_preserved_into_style_bible() -> None:
    from app.modules.v4.style_bible_service import StyleBibleServiceV4

    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        imported = service.import_result(
            project_id,
            external_import_payload(package, _output_with_character_entity_kind("human_group")),
        )
        character = imported["script_analysis"]["continuity_catalog"]["characters"][0]
        assert character["entity_kind"] == "human_group"

        style, _style_id = StyleBibleServiceV4(session).generate(
            project_id, imported["script_analysis_artifact_id"]
        )
        assert style["characters"][0]["entity_kind"] == "human_group"


def test_script_prompt_carries_arbitrary_free_form_narration_identity_without_backend_profile_mapping() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        project = ExternalScriptAnalysisService(session).projects.get(project_id)
        assert project is not None
        project.channel_dna_json["narration_identity"] = {
            "voice": ["philosophical minimalist second-person narrator"],
            "delivery": ["melancholic, sparse, and deliberately non-comedic"],
            "explanation_style": ["use concrete sensory images with very little narrator reaction"],
            "tone_boundaries": ["never become soothing bedtime narration"],
            "avoid": ["meme phrasing", "documentary announcer voice"],
        }
        session.commit()
        package = ExternalScriptAnalysisService(session).build_prompt(project_id, _request())
        prompt = package["prompt_text"]
        for value in project.channel_dna_json["narration_identity"]["voice"] + project.channel_dna_json["narration_identity"]["delivery"]:
            assert value in prompt
        assert package["prompt_version"] == "script_and_analysis_external_v17"


def test_pending_script_analysis_prompt_package_is_restored_after_reload() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        package = ExternalScriptAnalysisService(session).build_prompt(project_id, _request())

    with factory() as session:
        restored = ExternalScriptAnalysisService(session).get_pending_prompt(project_id)
        assert restored["generated"] is True
        assert restored["prompt_package"] == package
        assert restored["prompt_package"]["artifact_ids"] == package["artifact_ids"]
        assert restored["request_input"]["target_duration_minutes"] == 1
        assert restored["request_input"]["narration_wpm"] == {"min": 145, "max": 165}

def test_analysis_missing_required_collection_is_rejected() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = combined_script_analysis_output()
        del output["analysis"]["narrative_markers"]
        with pytest.raises(AppError) as exc_info:
            service.validate_import(project_id, external_import_payload(package, output))
        assert exc_info.value.error_code == "SCRIPT_ANALYSIS_EXTERNAL_OUTPUT_INVALID"


def test_analysis_rejects_extra_planned_wpm_field() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = combined_script_analysis_output()
        output["analysis"]["section_guidance"][0]["planned_narration_wpm"] = 160
        with pytest.raises(AppError) as exc_info:
            service.validate_import(project_id, external_import_payload(package, output))
        assert exc_info.value.error_code == "SCRIPT_ANALYSIS_EXTERNAL_OUTPUT_INVALID"


def test_script_import_preserves_llm_selected_section_wpm_within_range() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = combined_script_analysis_output()
        selected = [145, 155, 165]
        for section, wpm in zip(output["script"]["script_sections"], selected):
            section["planned_narration_wpm"] = wpm
        imported = service.import_result(project_id, external_import_payload(package, output))
        assert [section["planned_narration_wpm"] for section in imported["script"]["script_sections"]] == selected


def test_script_import_rejects_section_wpm_outside_request_range() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = combined_script_analysis_output()
        output["script"]["script_sections"][0]["planned_narration_wpm"] = 166
        with pytest.raises(AppError) as exc_info:
            service.import_result(project_id, external_import_payload(package, output))
        assert exc_info.value.error_code == "SCRIPT_SECTION_WPM_OUT_OF_RANGE"


def test_script_build_wpm_is_local_request_state_and_does_not_mutate_project_channel_dna() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        project = service.projects.get(project_id)
        assert project is not None
        channel_default = project.channel_dna_json["narration_wpm"]
        assert channel_default == {"min": 145, "max": 180}

        request = _request()
        request["user_input"]["narration_wpm"] = {"min": 160, "max": 180}
        package = service.build_prompt(project_id, request)

        project = service.projects.get(project_id)
        assert project is not None
        assert project.channel_dna_json["narration_wpm"] == channel_default
        assert '"min": 160' in package["prompt_text"]
        assert '"max": 180' in package["prompt_text"]
        restored = service.get_pending_prompt(project_id)
        assert restored["request_input"]["narration_wpm"] == {"min": 160, "max": 180}
        assert project.channel_dna_json["narration_wpm"] == channel_default

def test_hook_density_must_be_high() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = combined_script_analysis_output()
        output["analysis"]["section_guidance"][0]["pacing_density"] = "medium"
        with pytest.raises(AppError) as exc_info:
            service.import_result(project_id, external_import_payload(package, output))
        assert exc_info.value.error_code == "SCRIPT_HOOK_PACING_DENSITY_INVALID"
