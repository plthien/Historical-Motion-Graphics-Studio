from __future__ import annotations

import re
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.api.errors import AppError
from app.api.prompt_composer_schemas_v4 import PromptComposerVisualBeat
from app.modules.media.media_task_service import MediaTaskService
from app.modules.v4.prompt_composer import (
    compile_prompt_bundle,
    compile_reference_authority,
    compose_image_prompt,
    compose_negative_prompt,
)
from app.modules.v4.prompt_composer_input_builder import PromptComposerError


def _style_snapshot() -> dict:
    return {
        "visual_identity": {
            "medium": ["flat editorial animation frame", "clean vector-like illustration"],
            "linework": ["thin confident outlines", "simple expressive contour lines"],
            "rendering": ["matte cel shading", "soft shape-based shadows"],
            "palette": ["muted teal and amber accents", "controlled warm highlights"],
            "shape_language": ["rounded simplified forms", "readable silhouettes"],
            "texture": ["subtle paper grain", "minimal surface noise"],
            "editorial_language": ["switch representation mode when another honest form communicates the locked Beat more clearly"],
        },
        "entity_styles": {
            "character": {
                "appearance": ["simplified faces", "stable readable silhouettes"],
                "avoid": ["identity drift", "Photorealistic humans"],
            },
            "location": {
                "appearance": ["clear environmental silhouettes"],
                "avoid": ["overly complex background clutter"],
            },
            "prop": {
                "appearance": ["recognizable simple object designs"],
                "avoid": ["prop identity drift"],
            },
            "recurring_visual_subject": {
                "appearance": ["stable recurring subject design"],
                "avoid": ["recurring subject identity drift"],
            },
        },
        "global_negative_constraints": [
            "Photorealism",
            "Photorealistic humans",
            "Semi-realistic painterly illustration",
            "Painterly semi-realistic rendering",
            "Realistic documentary-style backgrounds",
            "multiple images",
            "title",
        ],
    }


def _binding(anchor_type: str, anchor_id: str, index: int) -> dict:
    return {
        "anchor_type": anchor_type,
        "anchor_id": anchor_id,
        "reference_artifact_id": f"artifact_000000000{100 + index:03d}",
        "media_artifact_id": f"artifact_000000000{200 + index:03d}",
        "approval_set_artifact_id": "artifact_000000000999",
        "source_definition_hash": f"sha256:{str(index) * 64}",
        "media_sha256": str(index + 1) * 64,
        "mime_type": "image/png",
        "image_artifact_id": f"artifact_000000000{300 + index:03d}",
    }


def _entities() -> dict:
    return {
        "characters": [
            {
                "char_id": "char_001",
                "label": "dependent child",
                "description": "A small child separated from a caregiver.",
                "explicit_visual_traits": ["small hunched posture"],
                "identity_markers": ["simple child silhouette"],
                "continuity_notes": ["Keep the same child identity."],
                "physical_constraints": ["child remains smaller than the caregiver"],
            },
            {
                "char_id": "char_002",
                "label": "absent caregiver",
                "description": "A caregiver remembered by the child.",
                "explicit_visual_traits": ["tall protective silhouette"],
                "identity_markers": ["calm adult posture"],
                "continuity_notes": ["Keep the same adult identity."],
                "physical_constraints": [],
            },
        ],
        "locations": [
            {
                "loc_id": "loc_001",
                "label": "temporary shelter",
                "description": "A sparse shelter with a low fire and open empty space.",
                "explicit_visual_traits": ["low fire ring", "open negative space"],
                "identity_markers": ["shelter arch silhouette"],
                "continuity_notes": ["Keep the shelter recognizable."],
                "physical_constraints": ["low shelter arch remains human scale"],
            }
        ],
        "recurring_props": [
            {
                "prop_id": "prop_001",
                "label": "small keepsake",
                "description": "A handmade token carried across scenes.",
                "explicit_visual_traits": ["simple oval token"],
                "identity_markers": ["single carved notch"],
                "continuity_notes": ["Keep the token recognizable."],
                "physical_constraints": ["one child can hold the token in one hand"],
            }
        ],
        "recurring_visual_subjects": [
            {
                "subject_id": "subject_001",
                "label": "distant signal",
                "description": "A recurring column of light in the distance.",
                "visual_type": "non_living",
                "explicit_visual_traits": ["thin vertical glow"],
                "continuity_notes": ["Keep the distant signal readable."],
                "physical_constraints": [],
            }
        ],
        "motifs": [{"motif_id": "motif_001", "description": "empty space as emotional pressure"}],
    }


def _beat(*, bindings: list[dict] | None = None, overrides: dict | None = None) -> dict:
    value = {
        "scene_id": "scene_001",
        "beat_id": "beat_001",
        "visual_context_ref": "ctx_001",
        "beat_order": 1,
        "local_beat_order": 1,
        "continuity_group_id": "continuity_001",
        "visual_priority": "normal",
        "is_narrative_marker": False,
        "representation_family": "character_environment_lived",
        "selected_character_refs": ["char_001"],
        "selected_location_refs": ["loc_001"],
        "selected_prop_refs": [],
        "selected_recurring_visual_subject_refs": [],
        "selected_motif_refs": [],
        "selected_native_visual_reference_ids": [],
        "visual_concept": "A dependent child reaches toward the empty side of the shelter.",
        "subject_action": "The child stretches one hand toward an absent caregiver just outside the frame.",
        "environment_emphasis": "The shelter feels sparse, with strong empty space around the child.",
        "framing": "medium_wide",
        "camera_angle": "eye_level",
        "camera_movement": "slow_push_in",
        "composition": "Strong empty-space composition, child held low in frame with room left for absence.",
        "lighting_direction": "warm localized lighting from the low fire",
        "color_emphasis": "muted amber against cool teal shadows",
        "motion_guidance": "Camera movement: slow_push_in.",
        "continuity_with_previous": "new_setup",
        "previous_beat_image_reference_required": False,
        "continuity_notes": "Continuity relation: new_setup.",
        "production_notes": "Repeat the visual identity and production constraints.",
        "negative_requirements": ["identity drift", "multiple images", "watermark"],
        "visible_text": [],
        "selected_reference_bindings": bindings or [],
    }
    value["selected_native_visual_reference_ids"] = [item["reference_artifact_id"] for item in value["selected_reference_bindings"]]
    for item in value["selected_reference_bindings"]:
        if item["anchor_type"] == "character" and item["anchor_id"] not in value["selected_character_refs"]:
            value["selected_character_refs"].append(item["anchor_id"])
        if item["anchor_type"] == "prop" and item["anchor_id"] not in value["selected_prop_refs"]:
            value["selected_prop_refs"].append(item["anchor_id"])
        if item["anchor_type"] == "recurring_visual_subject" and item["anchor_id"] not in value["selected_recurring_visual_subject_refs"]:
            value["selected_recurring_visual_subject_refs"].append(item["anchor_id"])
    if overrides:
        value.update(overrides)
    return value


def _payload() -> dict:
    return {
        "options": {
            "schema_version": "1.0",
            "prompt_language": "en",
            "aspect_ratio": "9:16",
            "target_asset_role": "beat_visual",
            "image_count": 1,
        },
        "visual_style_snapshot": _style_snapshot(),
        "style_entities": _entities(),
        "visual_context_catalog": {
            "ctx_001": {
                "mode": "representative_scenario",
                "timeframe": None,
                "setting": "temporary shelter",
                "constraints": ["Keep the scenario representative rather than documentary"],
            }
        },
    }


def _composer_input(beats: list[dict]) -> dict:
    return {
        "schema_version": "3.0",
        "project_id": "project_111111111111",
        "source_script_revision_id": "artifact_111111111111",
        "source_script_analysis_revision_id": "artifact_222222222222",
        "source_style_bible_revision_id": "artifact_333333333333",
        "source_scene_plan_revision_id": "artifact_444444444444",
        "source_visual_plan_revision_id": "artifact_555555555555",
        "source_visual_reference_approval_set_artifact_id": None,
        "prompt_bundle_revision_id": "artifact_666666666666",
        **_payload(),
        "visual_beats": beats,
    }


def _positions(text: str, headings: list[str]) -> list[int]:
    positions: list[int] = []
    for heading in headings:
        match = re.search(rf"(?m)^{re.escape(heading)}$", text)
        assert match is not None, f"missing heading {heading!r}"
        positions.append(match.start())
    return positions


def test_scene_asset_compiler_uses_canonical_skeleton_without_camera_duplication() -> None:
    bindings = [_binding("character", "char_001", 1), _binding("location", "loc_001", 2)]
    prompt = compose_image_prompt(_beat(bindings=bindings), _payload())

    headings = [
        "OUTPUT CONTRACT",
        "VISUAL CONTEXT",
        "STYLE AND REFERENCE PRECEDENCE",
        "SCENE",
        "REFERENCE AUTHORITY",
        "PHYSICAL CONSTRAINTS",
        "CAMERA AND COMPOSITION",
        "LIGHTING AND COLOR",
        "STYLE",
    ]
    assert _positions(prompt, headings) == sorted(_positions(prompt, headings))
    assert "Create exactly one native 9:16 image" in prompt
    assert "Generate a completely new full-frame composition from the SCENE and CAMERA AND COMPOSITION instructions below." in prompt
    assert "Use the full canvas edge-to-edge." in prompt
    assert "A dependent child reaches toward the empty side of the shelter." in prompt
    assert "Create a single still-image composition, not a storyboard panel." in prompt
    assert "Camera movement:" not in prompt
    assert "slow push in" not in prompt
    assert "motion_guidance" not in prompt
    assert "Camera movement: slow_push_in" not in prompt
    assert "CONTINUITY" not in prompt
    assert "VISUAL PRIORITY" not in prompt
    assert "Production constraints" not in prompt
    assert "Selected canonical characters" not in prompt
    assert "Selected location" not in prompt
    assert "Matching entity styles" not in prompt


@pytest.mark.parametrize("medium,rendering", [
    ("rough flat marker drawing", "minimal uneven fills"),
    ("high-detail oil painting", "layered naturalistic illumination"),
])
def test_global_style_governs_whole_scene_without_channel_hardcoding(
    medium: str, rendering: str,
) -> None:
    payload = _payload()
    payload["visual_style_snapshot"]["visual_identity"]["medium"] = [medium]
    payload["visual_style_snapshot"]["visual_identity"]["rendering"] = [rendering]
    beat = _beat(bindings=[_binding("character", "char_001", 1), _binding("location", "loc_001", 2)])
    prompt = compose_image_prompt(beat, payload)
    assert medium in prompt and rendering in prompt
    assert prompt.index("STYLE AND REFERENCE PRECEDENCE") < prompt.index("REFERENCE AUTHORITY")
    assert "one coherent mark-making and finish level" in prompt
    assert "render them using the current global visual style" in prompt
    assert "meme-stick" not in prompt


def test_reference_authority_character_and_location_semantics_follow_scene_and_camera() -> None:
    bindings = [_binding("character", "char_001", 1), _binding("location", "loc_001", 2)]
    prompt = compose_image_prompt(_beat(bindings=bindings), _payload())

    positions = dict(zip(
        ["SCENE", "REFERENCE AUTHORITY", "PHYSICAL CONSTRAINTS", "CAMERA AND COMPOSITION", "STYLE"],
        _positions(prompt, ["SCENE", "REFERENCE AUTHORITY", "PHYSICAL CONSTRAINTS", "CAMERA AND COMPOSITION", "STYLE"]),
        strict=True,
    ))
    assert positions["SCENE"] < positions["REFERENCE AUTHORITY"] < positions["PHYSICAL CONSTRAINTS"] < positions["CAMERA AND COMPOSITION"] < positions["STYLE"]
    assert 'Use @char_001 as the exact visual identity authority for "dependent child"' in prompt
    assert "Pose, expression, camera angle, and action may change for this beat; identity must not." in prompt
    assert 'Use @loc_001 only as the stable location-identity reference for "temporary shelter"' in prompt
    assert "Build a completely new environment composition for this beat." in prompt
    assert "Do not use @loc_001 as a background, base image, scene template, composition template, or source canvas." in prompt
    assert "not a background, base canvas, scene template, or composition authority." in prompt
    assert "Preserve its recognizable architecture, recurring objects, palette, materials" not in prompt
    assert "recurring environment identity and visual-world authority" not in prompt


def test_prompt_bundle_declares_prev_beat_requirement_only_for_continuous_beats() -> None:
    beat_a = _beat()
    beat_b = _beat(overrides={
        "beat_id": "beat_002",
        "beat_order": 2,
        "local_beat_order": 2,
        "continuity_with_previous": "same_setup",
        "previous_beat_image_reference_required": True,
        "continuity_notes": "Continuity relation: same_setup.",
    })

    bundle = compile_prompt_bundle(prompt_composer_input=_composer_input([beat_a, beat_b]))
    first, second = bundle["prompt_items"]

    assert first["continuity_reference_requirement"] is None
    assert "@prev_beat" not in first["image_prompt"]
    assert "CONTINUITY" not in first["image_prompt"]
    assert second["continuity_reference_requirement"] == {
        "reference_tag": "prev_beat",
        "source_beat_id": "beat_001",
        "relation": "same_setup",
    }
    assert "CONTINUITY" in second["image_prompt"]
    assert second["image_prompt"].index("@prev_beat") > second["image_prompt"].index("REFERENCE AUTHORITY")
    assert _positions(second["image_prompt"], ["REFERENCE AUTHORITY", "CONTINUITY", "STYLE"]) == sorted(
        _positions(second["image_prompt"], ["REFERENCE AUTHORITY", "CONTINUITY", "STYLE"])
    )
    assert "Use @prev_beat as the approved immediately previous-beat continuity reference." in second["image_prompt"]
    assert "Preserve only the transient visual continuity required by this relation" in second["image_prompt"]
    assert "Canonical character and location references remain the authority for intrinsic identity." in second["image_prompt"]
    assert "Continuity relation: same_setup" not in second["image_prompt"]


@pytest.mark.parametrize("required", [False, True])
def test_prompt_composer_visual_beat_accepts_same_setup_with_either_dependency(required: bool) -> None:
    parsed = PromptComposerVisualBeat.model_validate(
        _beat(
            overrides={
                "continuity_with_previous": "same_setup",
                "previous_beat_image_reference_required": required,
                "continuity_notes": "Continuity relation: same_setup.",
            }
        )
    )

    assert parsed.previous_beat_image_reference_required is required


@pytest.mark.parametrize(
    ("relation", "required"),
    [("new_setup", True), ("match_composition", False)],
)
def test_prompt_composer_visual_beat_rejects_invalid_dependency_matrix(
    relation: str,
    required: bool,
) -> None:
    with pytest.raises(ValidationError):
        PromptComposerVisualBeat.model_validate(
            _beat(
                overrides={
                    "continuity_with_previous": relation,
                    "previous_beat_image_reference_required": required,
                    "continuity_notes": f"Continuity relation: {relation}.",
                }
            )
        )


def test_prompt_bundle_prev_beat_requirement_can_cross_scene_boundary() -> None:
    beat_a = _beat()
    beat_b = _beat(
        overrides={
            "scene_id": "scene_002",
            "beat_id": "beat_002",
            "beat_order": 2,
            "local_beat_order": 1,
            "continuity_group_id": "continuity_001",
            "continuity_with_previous": "match_subject",
            "previous_beat_image_reference_required": True,
            "continuity_notes": "Continuity relation: match_subject.",
        }
    )

    bundle = compile_prompt_bundle(
        prompt_composer_input=_composer_input([beat_a, beat_b])
    )
    second = bundle["prompt_items"][1]

    assert second["scene_id"] == "scene_002"
    assert second["continuity_reference_requirement"] == {
        "reference_tag": "prev_beat",
        "source_beat_id": "beat_001",
        "relation": "match_subject",
    }
    assert "@prev_beat" in second["image_prompt"]


def test_semantic_continuity_without_dependency_can_cross_scene_boundary() -> None:
    beat_a = _beat()
    beat_b = _beat(
        overrides={
            "scene_id": "scene_002",
            "beat_id": "beat_002",
            "beat_order": 2,
            "local_beat_order": 1,
            "continuity_group_id": "continuity_001",
            "continuity_with_previous": "match_subject",
            "previous_beat_image_reference_required": False,
            "continuity_notes": "Continuity relation: match_subject.",
        }
    )

    second = compile_prompt_bundle(prompt_composer_input=_composer_input([beat_a, beat_b]))["prompt_items"][1]

    assert beat_b["continuity_with_previous"] == "match_subject"
    assert second["continuity_reference_requirement"] is None
    assert "@prev_beat" not in second["image_prompt"]


@pytest.mark.parametrize(
    ("relation", "required_text", "forbidden_text"),
    [
        (
            "same_setup",
            "ongoing visible subject identities, environment/location identity, and basic spatial setup",
            None,
        ),
        (
            "match_subject",
            "relevant subject, object, or character identity and ongoing visible state",
            "Do not carry unrelated location/environment or prop content",
        ),
        (
            "match_location",
            "relevant environment/location identity and spatial context",
            "Do not carry unrelated characters, subjects, or props",
        ),
        (
            "match_composition",
            "only as a geometry reference for framing, placement, scale, negative space, and spatial arrangement",
            "Do not copy prior physical subjects, characters, locations, or props merely because they appear in @prev_beat",
        ),
        (
            "graphic_sequence",
            "only for editorial sequence and layout grammar",
            "Do not carry physical characters, subjects, environments, or props",
        ),
    ],
)
def test_final_image_prompt_scopes_previous_beat_by_continuity_relation(
    relation: str,
    required_text: str,
    forbidden_text: str | None,
) -> None:
    prompt = compose_image_prompt(
        _beat(overrides={
            "continuity_with_previous": relation,
            "previous_beat_image_reference_required": True,
            "continuity_notes": f"Continuity relation: {relation}.",
        }),
        _payload(),
    )

    assert "@prev_beat" in prompt
    assert required_text in prompt
    if forbidden_text:
        assert forbidden_text in prompt


def test_first_beat_cannot_declare_previous_beat_runtime_dependency() -> None:
    beat = _beat(overrides={
        "continuity_with_previous": "match_subject",
        "previous_beat_image_reference_required": True,
        "continuity_notes": "Continuity relation: match_subject.",
    })

    with pytest.raises(PromptComposerError) as exc:
        compile_prompt_bundle(prompt_composer_input=_composer_input([beat]))

    assert exc.value.code == "PROMPT_COMPOSER_CONTINUITY_SOURCE_INVALID"


def test_same_setup_without_image_dependency_emits_no_previous_beat_reference() -> None:
    beat_a = _beat()
    beat_b = _beat(overrides={
        "beat_id": "beat_002",
        "beat_order": 2,
        "local_beat_order": 2,
        "continuity_with_previous": "same_setup",
        "previous_beat_image_reference_required": False,
        "continuity_notes": "Continuity relation: same_setup.",
    })

    second = compile_prompt_bundle(prompt_composer_input=_composer_input([beat_a, beat_b]))["prompt_items"][1]

    assert second["continuity_reference_requirement"] is None
    assert "@prev_beat" not in second["image_prompt"]
    assert "CONTINUITY" not in second["image_prompt"]


def test_prompt_composer_input_requires_explicit_previous_image_dependency() -> None:
    beat = _beat()
    del beat["previous_beat_image_reference_required"]

    with pytest.raises(PromptComposerError) as exc:
        compile_prompt_bundle(prompt_composer_input=_composer_input([beat]))

    assert exc.value.code == "PROMPT_COMPOSER_INPUT_INVALID"


def test_prev_beat_reference_tag_is_not_globally_accepted_for_media_tasks() -> None:
    service = MediaTaskService.__new__(MediaTaskService)

    with pytest.raises(AppError) as exc:
        service._validate_reference_image_descriptors(
            ({
                "reference_tag": "prev_beat",
                "artifact_id": "artifact_777777777777",
                "sha256": "a" * 64,
                "mime_type": "image/png",
            },),
            project_id="project_111111111111",
        )

    assert exc.value.error_code == "MEDIA_REFERENCE_IMAGE_INVALID"


def test_unreferenced_entity_metadata_is_compact_and_does_not_invent_traits() -> None:
    prompt = compose_image_prompt(_beat(bindings=[_binding("character", "char_001", 1)]), _payload())

    assert 'Use @char_001 as the exact visual identity authority for "dependent child"' in prompt
    assert 'Use compact approved text metadata for unreferenced location "temporary shelter"' in prompt
    for invented in ("white hair", "blue eyes", "red garment", "brown hide wrap"):
        assert invented not in prompt.lower()


def test_reference_authority_keeps_multiple_identities_and_prop_scope_separate() -> None:
    beat = _beat(
        bindings=[
            _binding("character", "char_001", 1),
            _binding("character", "char_002", 2),
            _binding("location", "loc_001", 3),
            _binding("prop", "prop_001", 4),
        ],
        overrides={"selected_character_refs": ["char_001", "char_002"], "selected_prop_refs": ["prop_001"]},
    )
    prompt = compose_image_prompt(beat, _payload())

    assert prompt.count("Use @char_001 as the exact visual identity authority") == 1
    assert prompt.count("Use @char_002 as the exact visual identity authority") == 1
    assert 'Use @prop_001 as the exact visual identity authority for "small keepsake"' in prompt
    assert "apparent size in frame" in prompt
    assert "intrinsic proportions, real-world relative scale, graspability" in prompt
    assert "stable location-identity reference" in prompt


def test_physical_constraints_keep_selected_entity_ownership_and_order() -> None:
    payload = _payload()
    payload["style_entities"]["characters"][0]["physical_constraints"] = [
        "shared physical rule",
        "child remains smaller than the caregiver",
    ]
    payload["style_entities"]["locations"][0]["physical_constraints"] = [
        "low shelter arch remains human scale",
    ]
    payload["style_entities"]["recurring_props"][0]["physical_constraints"] = [
        "shared physical rule",
        "one child can hold the token in one hand",
    ]
    payload["style_entities"]["recurring_visual_subjects"][0]["physical_constraints"] = [
        "signal remains fixed to the horizon",
    ]
    beat = _beat(
        overrides={
            "selected_prop_refs": ["prop_001"],
            "selected_recurring_visual_subject_refs": ["subject_001"],
        }
    )

    prompt = compose_image_prompt(beat, payload)
    block = prompt.split("PHYSICAL CONSTRAINTS\n\n", 1)[1].split(
        "\n\nCAMERA AND COMPOSITION", 1
    )[0]

    owners = [
        'For character char_001 "dependent child":',
        'For location loc_001 "temporary shelter":',
        'For prop prop_001 "small keepsake":',
        'For recurring visual subject subject_001 "distant signal":',
    ]
    assert _positions(block, owners) == sorted(_positions(block, owners))
    assert block.count("- shared physical rule.") == 2
    assert block.index('For character char_001') < block.index("- child remains smaller")
    assert block.index('For prop prop_001') < block.rindex("- shared physical rule.")
    assert "signal remains fixed to the horizon" in block.lower()


def test_physical_constraints_exclude_unselected_and_empty_entities() -> None:
    payload = _payload()
    payload["style_entities"]["characters"][1]["physical_constraints"] = [
        "unselected adult constraint",
    ]
    prompt = compose_image_prompt(_beat(), payload)
    assert "unselected adult constraint" not in prompt
    assert "@char_002" not in prompt

    empty_prompt = compose_image_prompt(
        _beat(
            overrides={
                "selected_character_refs": ["char_002"],
                "selected_location_refs": [],
            }
        ),
        _payload(),
    )
    assert "PHYSICAL CONSTRAINTS" not in empty_prompt
    assert 'For character char_002 "absent caregiver":' not in empty_prompt


def test_unbound_physical_constraint_owners_do_not_create_reference_tags() -> None:
    beat = _beat(overrides={"selected_prop_refs": ["prop_001"]})
    bundle = compile_prompt_bundle(prompt_composer_input=_composer_input([beat]))
    prompt = bundle["prompt_items"][0]["image_prompt"]

    assert 'For prop prop_001 "small keepsake":' in prompt
    assert "@prop_001" not in prompt
    assert bundle["prompt_items"][0]["reference_bindings"] == []


def test_physical_constraints_change_prompt_hash_deterministically() -> None:
    original_input = _composer_input([_beat(overrides={"selected_prop_refs": ["prop_001"]})])
    first = compile_prompt_bundle(prompt_composer_input=deepcopy(original_input))
    repeated = compile_prompt_bundle(prompt_composer_input=deepcopy(original_input))
    changed_input = deepcopy(original_input)
    changed_input["style_entities"]["recurring_props"][0]["physical_constraints"][0] = (
        "two hands are required to hold the token"
    )
    changed = compile_prompt_bundle(prompt_composer_input=changed_input)

    assert first["prompt_items"][0]["image_prompt"] == repeated["prompt_items"][0]["image_prompt"]
    assert first["prompt_items"][0]["prompt_hash"] == repeated["prompt_items"][0]["prompt_hash"]
    assert first["prompt_items"][0]["image_prompt"] != changed["prompt_items"][0]["image_prompt"]
    assert first["prompt_items"][0]["prompt_hash"] != changed["prompt_items"][0]["prompt_hash"]


def test_global_style_change_updates_hash_without_rewriting_reference_bindings() -> None:
    bindings = [_binding("character", "char_001", 1), _binding("location", "loc_001", 2)]
    original_input = _composer_input([_beat(bindings=bindings)])
    original_input["source_visual_reference_approval_set_artifact_id"] = "artifact_000000000999"
    first = compile_prompt_bundle(prompt_composer_input=deepcopy(original_input))["prompt_items"][0]
    changed_input = deepcopy(original_input)
    changed_input["visual_style_snapshot"]["visual_identity"]["rendering"] = ["new approved rendering"]
    changed = compile_prompt_bundle(prompt_composer_input=changed_input)["prompt_items"][0]
    assert first["prompt_hash"] != changed["prompt_hash"]
    assert first["reference_bindings"] == changed["reference_bindings"] == bindings
    assert "new approved rendering" in changed["image_prompt"]
    assert "new approved rendering" not in first["image_prompt"]


def test_visual_context_remains_separate_and_overrides_incompatible_previous_world() -> None:
    prompt = compose_image_prompt(
        _beat(
            overrides={
                "continuity_with_previous": "match_composition",
                "previous_beat_image_reference_required": True,
                "continuity_notes": "Continuity relation: match_composition.",
            }
        ),
        _payload(),
    )

    assert _positions(prompt, ["VISUAL CONTEXT", "PHYSICAL CONSTRAINTS", "CONTINUITY"]) == sorted(
        _positions(prompt, ["VISUAL CONTEXT", "PHYSICAL CONSTRAINTS", "CONTINUITY"])
    )
    assert "Previous-render carry must never import a setting, period, evidence status, or world cue" in prompt
    physical_block = prompt.split("PHYSICAL CONSTRAINTS\n\n", 1)[1].split(
        "\n\nCAMERA AND COMPOSITION", 1
    )[0]
    assert "Keep the scenario representative rather than documentary" not in physical_block


def test_scene_asset_still_prompt_does_not_consume_camera_movement() -> None:
    prompt = compose_image_prompt(
        _beat(overrides={"camera_movement": "slow_pull_out", "motion_guidance": "Camera movement: slow_pull_out."}),
        _payload(),
    )

    assert "slow pull out" not in prompt
    assert "slow_pull_out" not in prompt
    assert "Camera movement:" not in prompt


def test_unknown_reference_anchor_type_fails_deterministically() -> None:
    with pytest.raises(PromptComposerError) as exc:
        compile_reference_authority("vehicle", "veh_001", {"label": "truck"})
    assert exc.value.code == "PROMPT_COMPOSER_UNKNOWN_REFERENCE_ANCHOR_TYPE"


def test_negative_prompt_is_compact_and_provider_prompt_has_single_avoid_owner() -> None:
    bindings = [_binding("character", "char_001", 1), _binding("location", "loc_001", 2)]
    beat = _beat(bindings=bindings)
    image_prompt = compose_image_prompt(beat, _payload())
    negative = compose_negative_prompt(beat, _payload())
    provider_prompt = MediaTaskService._build_provider_prompt(image_prompt, negative)

    assert image_prompt.count("AVOID") == 0
    assert _positions(image_prompt, ["STYLE"])
    assert provider_prompt.count("Avoid:") == 1
    assert len(re.findall(r"\bAvoid\b|AVOID", provider_prompt)) == 1
    assert provider_prompt.index("STYLE") < provider_prompt.index("Avoid:")
    assert negative.count("photorealistic") == 1
    assert "semi-realistic" in negative
    assert "painterly" in negative
    assert "documentary" in negative
    assert "multiple images" not in negative
    assert "title" not in negative
    assert negative.lower().count("identity drift") == 1


def test_output_contract_blocks_reference_images_as_templates() -> None:
    prompt = compose_image_prompt(_beat(bindings=[_binding("location", "loc_001", 2)]), _payload())

    output_contract = prompt.split("\n\nSCENE\n\n", 1)[0]
    assert "Reference images are identity/design sources only." in output_contract
    assert "Use the full canvas edge-to-edge." in output_contract
    assert "background" in output_contract
    assert "base canvas" in output_contract
    assert "scene template" in output_contract
    assert "embed" in output_contract
    assert "inset image" in output_contract


def test_prompt_item_reference_bindings_still_match_prompt_tags() -> None:
    bindings = [
        _binding("character", "char_001", 1),
        _binding("location", "loc_001", 2),
        _binding("prop", "prop_001", 3),
        _binding("recurring_visual_subject", "subject_001", 4),
    ]
    beat = _beat(
        bindings=bindings,
        overrides={
            "selected_prop_refs": ["prop_001"],
            "selected_recurring_visual_subject_refs": ["subject_001"],
        },
    )

    composer_input = _composer_input([beat])
    composer_input["source_visual_reference_approval_set_artifact_id"] = "artifact_000000000999"
    bundle = compile_prompt_bundle(prompt_composer_input=composer_input)
    item = bundle["prompt_items"][0]

    assert item["reference_bindings"] == bindings
    assert set(re.findall(r"@((?:char|loc|prop|subject)_[0-9]{3})\b", item["image_prompt"])) == {
        binding["anchor_id"] for binding in bindings
    }


def test_representative_prompt_size_guardrail() -> None:
    bindings = [_binding("character", "char_001", 1), _binding("location", "loc_001", 2)]
    image_prompt = compose_image_prompt(_beat(bindings=bindings), _payload())
    negative = compose_negative_prompt(_beat(bindings=bindings), _payload())
    provider_prompt = MediaTaskService._build_provider_prompt(image_prompt, negative)
    legacy_like_prompt = "\n".join(
        [
            "Approved reference identity: " + "dependent child stable identity temporary shelter stable identity " * 20,
            "Selected canonical characters: " + "dependent child stable identity " * 20,
            "Selected location: " + "temporary shelter stable identity " * 20,
            "Visual identity: " + "medium linework rendering palette shape language texture " * 30,
            "Matching entity styles: " + "character appearance location appearance prop appearance " * 30,
            "Production constraints: " + "medium linework rendering palette shape language texture " * 30,
            "Continuity constraints: new setup Continuity relation: new_setup.",
        ]
    )

    assert len(provider_prompt.split()) < 800
    assert len(provider_prompt.split()) < len(legacy_like_prompt.split()) * 0.75
    assert len(provider_prompt) < len(legacy_like_prompt) * 0.75
