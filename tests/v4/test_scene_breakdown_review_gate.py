from __future__ import annotations

from copy import deepcopy

import pytest

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.session import build_session_factory
from app.modules.v4.scene_breakdown_review import editable_section_from_paired_plans
from app.modules.v4 import scene_breakdown_service as scene_breakdown_service_module
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from tests.v4.combined_scene_breakdown_support import generate_combined_pair


def _validate_request(result: dict, section: dict) -> dict:
    return {
        "expected_scene_plan_artifact_id": result["scene_plan_artifact_id"],
        "expected_visual_plan_artifact_id": result["visual_plan_artifact_id"],
        "section": section,
    }


def _editable(result: dict, section_id: str = "sec_001") -> dict:
    return editable_section_from_paired_plans(
        result["scene_plan"], result["visual_plan"], section_id
    )


def _set_continuity(visual: dict, relation: str) -> None:
    visual["continuity_with_previous"] = relation
    visual["previous_beat_image_reference_required"] = relation in {"same_setup", "match_composition"}


def test_review_validation_rejects_added_field_and_changed_array_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result)
    section["scenes"][0]["unexpected"] = "not allowed"
    section["scenes"][0]["beats"].pop()
    factory = build_session_factory(get_settings())
    with factory() as session:
        response = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"], "sec_001", _validate_request(result, section)
        )
    assert response["valid"] is False
    codes = {item["code"] for item in response["errors"]}
    assert "edit_field_added" in codes
    assert "edit_array_length_changed" in codes


def test_review_validation_rejects_section_id_or_source_partition_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    factory = build_session_factory(get_settings())

    changed_id = _editable(result)
    changed_id["section_id"] = "sec_999"
    with factory() as session:
        response = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"], "sec_001", _validate_request(result, changed_id)
        )
    assert response["valid"] is False
    assert any(item["code"] == "section_id_changed" for item in response["errors"])

    changed_text = _editable(result)
    changed_text["scenes"][0]["beats"][0]["script_text"] = "Not authoritative narration."
    with factory() as session:
        response = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"], "sec_001", _validate_request(result, changed_text)
        )
    assert response["valid"] is False
    assert any(
        item["code"] == "edit_source_ownership_changed"
        for item in response["errors"]
    )


def test_review_save_requires_validation_of_exact_paired_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result)
    factory = build_session_factory(get_settings())
    with factory() as session:
        validated = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"], "sec_001", _validate_request(result, section)
        )
    assert validated["valid"] is True

    changed = deepcopy(section)
    changed["scenes"][0]["beats"][0]["visual"]["composition"] = "Changed after validation."
    with factory() as session:
        with pytest.raises(AppError) as exc:
            SceneBreakdownServiceV4(session).save_section_edit(
                result["project_id"],
                "sec_001",
                {
                    **_validate_request(result, changed),
                    "validation_artifact_id": validated["validation_artifact_id"],
                },
            )
    assert exc.value.error_code == "SCENE_BREAKDOWN_EDIT_VALIDATION_STALE"


def test_review_validate_save_and_approve_create_new_paired_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result)
    long_composition = "UNIQUE_START_" + (
        "Detailed reviewed composition preserves exact placement, scale, contact geometry, separated group depth, "
        "current-state staging, readable negative space, and anti-compositing constraints across reconstruction. "
        * 4
    ) + "_UNIQUE_END"
    section["scenes"][-1]["beats"][-1]["visual"]["composition"] = long_composition
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = SceneBreakdownServiceV4(session)
        validated = service.validate_section_edit(
            result["project_id"], "sec_001", _validate_request(result, section)
        )
    assert validated["valid"] is True

    with factory() as session:
        saved = SceneBreakdownServiceV4(session).save_section_edit(
            result["project_id"],
            "sec_001",
            {
                **_validate_request(result, section),
                "validation_artifact_id": validated["validation_artifact_id"],
            },
        )
    assert saved["scene_plan_artifact_id"] != result["scene_plan_artifact_id"]
    assert saved["visual_plan_artifact_id"] != result["visual_plan_artifact_id"]
    assert saved["visual_plan"]["source_scene_plan_revision_id"] == saved["scene_plan_artifact_id"]
    assert saved["scene_plan"]["status"] == "draft_ready"
    assert saved["visual_plan"]["status"] == "draft_ready"
    assert len(long_composition) > 600
    saved_tail = next(
        scene
        for scene in reversed(saved["visual_plan"]["scenes"])
        if scene["section_id"] == "sec_001"
    )["beat_plans"][-1]
    assert saved_tail["composition"] == long_composition

    captured_carry: list[dict | None] = []
    original_build_section_input = scene_breakdown_service_module.build_section_input

    def capture_build_section_input(**kwargs):
        if kwargs["section"].section_id == "sec_002":
            captured_carry.append(deepcopy(kwargs.get("carry_over")))
        return original_build_section_input(**kwargs)

    monkeypatch.setattr(
        scene_breakdown_service_module,
        "build_section_input",
        capture_build_section_input,
    )
    later_section = _editable(saved, "sec_002")
    with factory() as session:
        later_validated = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_002",
            _validate_request(saved, later_section),
        )
    assert later_validated["valid"] is True
    assert captured_carry[-1]["previous_visual_tail"]["composition"] == long_composition

    with factory() as session:
        approved = SceneBreakdownServiceV4(session).approve(
            result["project_id"],
            {
                "expected_scene_plan_artifact_id": saved["scene_plan_artifact_id"],
                "expected_visual_plan_artifact_id": saved["visual_plan_artifact_id"],
                "aspect_ratio": "16:9",
            },
        )
    assert approved["scene_plan"]["status"] == "approved"
    assert approved["visual_plan"]["status"] == "approved"
    assert approved["visual_plan"]["source_scene_plan_revision_id"] == approved["scene_plan_artifact_id"]
    approved_tail = next(
        scene
        for scene in reversed(approved["visual_plan"]["scenes"])
        if scene["section_id"] == "sec_001"
    )["beat_plans"][-1]
    assert approved_tail["composition"] == long_composition


@pytest.mark.parametrize(
    "relation",
    ["same_setup", "match_subject", "match_location"],
)
def test_review_validation_preserves_cross_section_continuity_relations(
    monkeypatch: pytest.MonkeyPatch,
    relation: str,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result, "sec_002")
    _set_continuity(section["scenes"][0]["beats"][0]["visual"], relation)

    factory = build_session_factory(get_settings())
    with factory() as session:
        validated = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_002",
            _validate_request(result, section),
        )

    assert validated["valid"] is True
    assert validated["validation_artifact_id"]


def test_review_validation_allows_new_setup_at_section_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result, "sec_002")
    _set_continuity(section["scenes"][0]["beats"][0]["visual"], "new_setup")

    factory = build_session_factory(get_settings())
    with factory() as session:
        validated = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_002",
            _validate_request(result, section),
        )

    assert validated["valid"] is True


def test_review_validate_save_round_trip_preserves_cross_section_same_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result, "sec_002")
    _set_continuity(section["scenes"][0]["beats"][0]["visual"], "same_setup")

    factory = build_session_factory(get_settings())
    with factory() as session:
        service = SceneBreakdownServiceV4(session)
        validated = service.validate_section_edit(
            result["project_id"],
            "sec_002",
            _validate_request(result, section),
        )
    assert validated["valid"] is True

    with factory() as session:
        saved = SceneBreakdownServiceV4(session).save_section_edit(
            result["project_id"],
            "sec_002",
            {
                **_validate_request(result, section),
                "validation_artifact_id": validated["validation_artifact_id"],
            },
        )

    saved_section = editable_section_from_paired_plans(
        saved["scene_plan"],
        saved["visual_plan"],
        "sec_002",
    )
    assert (
        saved_section["scenes"][0]["beats"][0]["visual"]["continuity_with_previous"]
        == "same_setup"
    )

    sec1_last = next(
        scene
        for scene in reversed(saved["visual_plan"]["scenes"])
        if scene["section_id"] == "sec_001"
    )["beat_plans"][-1]
    sec2_first = next(
        scene
        for scene in saved["visual_plan"]["scenes"]
        if scene["section_id"] == "sec_002"
    )["beat_plans"][0]
    assert sec2_first["continuity_group_id"] == sec1_last["continuity_group_id"]

    with factory() as session:
        approved = SceneBreakdownServiceV4(session).approve(
            result["project_id"],
            {
                "expected_scene_plan_artifact_id": saved["scene_plan_artifact_id"],
                "expected_visual_plan_artifact_id": saved["visual_plan_artifact_id"],
                "aspect_ratio": "16:9",
            },
        )
    assert approved["scene_plan"]["status"] == "approved"
    assert approved["visual_plan"]["status"] == "approved"


def test_review_new_setup_starts_new_cross_section_continuity_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result, "sec_002")
    _set_continuity(section["scenes"][0]["beats"][0]["visual"], "new_setup")

    factory = build_session_factory(get_settings())
    with factory() as session:
        validated = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_002",
            _validate_request(result, section),
        )
    assert validated["valid"] is True

    with factory() as session:
        saved = SceneBreakdownServiceV4(session).save_section_edit(
            result["project_id"],
            "sec_002",
            {
                **_validate_request(result, section),
                "validation_artifact_id": validated["validation_artifact_id"],
            },
        )

    sec1_last = next(
        scene
        for scene in reversed(saved["visual_plan"]["scenes"])
        if scene["section_id"] == "sec_001"
    )["beat_plans"][-1]
    sec2_first = next(
        scene
        for scene in saved["visual_plan"]["scenes"]
        if scene["section_id"] == "sec_002"
    )["beat_plans"][0]
    assert sec2_first["continuity_group_id"] != sec1_last["continuity_group_id"]


def _fake_previous_char_carry() -> dict:
    return {
        "previous_visual_tail": {
            "visual_concept": "The recurring person remains visible.",
            "selected_character_refs": ["char_001"],
            "selected_location_refs": [],
            "selected_prop_refs": [],
            "selected_recurring_visual_subject_refs": [],
            "selected_motif_refs": [],
            "framing": "medium",
            "composition": "The recurring person remains centered.",
            "visual_state": "The recurring person remains visible.",
        },
    }


def _install_known_char_without_direct_eligibility(
    monkeypatch: pytest.MonkeyPatch,
    template: dict,
) -> None:
    original = scene_breakdown_service_module.build_section_input

    def wrapped_build_section_input(**kwargs):
        payload = original(**kwargs)
        payload["visual_context"]["reference_catalog"]["characters"]["char_001"] = {
            "label": "Recurring person",
        }
        return payload

    monkeypatch.setattr(
        scene_breakdown_service_module,
        "build_section_input",
        wrapped_build_section_input,
    )
    monkeypatch.setattr(
        scene_breakdown_service_module,
        "editable_section_from_paired_plans",
        lambda scene_plan, visual_plan, section_id: deepcopy(template),
    )
    monkeypatch.setattr(
        SceneBreakdownServiceV4,
        "_validated_carry_before_section",
        lambda self, **kwargs: _fake_previous_char_carry(),
    )


def test_review_same_setup_can_use_immediate_previous_section_reference_carry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result, "sec_002")
    first_visual = section["scenes"][0]["beats"][0]["visual"]
    _set_continuity(first_visual, "same_setup")
    first_visual["selected_character_refs"] = ["char_001"]
    _install_known_char_without_direct_eligibility(monkeypatch, section)

    factory = build_session_factory(get_settings())
    with factory() as session:
        validated = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_002",
            _validate_request(result, section),
        )

    assert validated["valid"] is True


def test_review_new_setup_clears_previous_section_transient_reference_carry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result, "sec_002")
    first_visual = section["scenes"][0]["beats"][0]["visual"]
    _set_continuity(first_visual, "new_setup")
    first_visual["selected_character_refs"] = ["char_001"]
    _install_known_char_without_direct_eligibility(monkeypatch, section)

    factory = build_session_factory(get_settings())
    with factory() as session:
        validated = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_002",
            _validate_request(result, section),
        )

    assert validated["valid"] is False
    assert any(
        item["code"] == "visual_reference_unsupported"
        for item in validated["errors"]
    )


def _move_prefix_between_beats(left: dict, right: dict) -> None:
    right_text = str(right["script_text"])
    split = right_text.find(" ", 1)
    if split <= 0:
        split = max(1, len(right_text) // 2)
    moved = right_text[:split]
    left["script_text"] = str(left["script_text"]) + moved
    right["script_text"] = right_text[split:]


def _source_error_codes(response: dict) -> set[str]:
    return {
        str(item.get("code"))
        for item in response.get("errors", [])
        if isinstance(item, dict)
    }


def test_review_rejects_word_move_while_preserving_beat_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result)
    beats = section["scenes"][0]["beats"]
    assert len(beats) >= 2
    original = str(beats[0]["script_text"]) + str(beats[1]["script_text"])
    _move_prefix_between_beats(beats[0], beats[1])
    assert str(beats[0]["script_text"]) + str(beats[1]["script_text"]) == original

    factory = build_session_factory(get_settings())
    with factory() as session:
        response = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_001",
            _validate_request(result, section),
        )

    assert response["valid"] is False
    assert "edit_source_ownership_changed" in _source_error_codes(response)


def test_review_rejects_swapped_beat_source_with_same_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result)
    beats = section["scenes"][0]["beats"]
    assert len(beats) >= 2
    beats[0]["script_text"], beats[1]["script_text"] = (
        beats[1]["script_text"],
        beats[0]["script_text"],
    )

    factory = build_session_factory(get_settings())
    with factory() as session:
        response = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_001",
            _validate_request(result, section),
        )

    assert response["valid"] is False
    assert "edit_source_ownership_changed" in _source_error_codes(response)


def test_review_rejects_source_move_across_scene_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    section = _editable(result)
    assert len(section["scenes"]) >= 2
    left = section["scenes"][0]["beats"][-1]
    right = section["scenes"][1]["beats"][0]
    original = str(left["script_text"]) + str(right["script_text"])
    _move_prefix_between_beats(left, right)
    assert str(left["script_text"]) + str(right["script_text"]) == original

    factory = build_session_factory(get_settings())
    with factory() as session:
        response = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_001",
            _validate_request(result, section),
        )

    assert response["valid"] is False
    assert "edit_source_ownership_changed" in _source_error_codes(response)


def test_review_rejects_punctuation_only_boundary_move() -> None:
    from app.modules.v4.scene_breakdown_review import source_ownership_issues

    template = {
        "scenes": [
            {
                "beats": [
                    {"script_text": "Alpha."},
                    {"script_text": " Beta"},
                ]
            }
        ]
    }
    candidate = deepcopy(template)
    candidate["scenes"][0]["beats"][0]["script_text"] = "Alpha"
    candidate["scenes"][0]["beats"][1]["script_text"] = ". Beta"

    issues = source_ownership_issues(template, candidate)

    assert {
        item["code"]
        for item in issues
    } == {"edit_source_ownership_changed"}


def test_review_save_resegmentation_has_specific_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    original = _editable(result)
    factory = build_session_factory(get_settings())
    with factory() as session:
        validated = SceneBreakdownServiceV4(session).validate_section_edit(
            result["project_id"],
            "sec_001",
            _validate_request(result, original),
        )
    assert validated["valid"] is True

    changed = deepcopy(original)
    beats = changed["scenes"][0]["beats"]
    assert len(beats) >= 2
    _move_prefix_between_beats(beats[0], beats[1])

    with factory() as session:
        with pytest.raises(AppError) as exc_info:
            SceneBreakdownServiceV4(session).save_section_edit(
                result["project_id"],
                "sec_001",
                {
                    **_validate_request(result, changed),
                    "validation_artifact_id": validated["validation_artifact_id"],
                },
            )

    assert (
        exc_info.value.error_code
        == "SCENE_BREAKDOWN_EDIT_RESEGMENTATION_FORBIDDEN"
    )
