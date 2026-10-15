from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.modules.v4.visual_reference_prompt_compiler import (
    build_visual_reference_inventory,
)
from app.api.errors import AppError


GLOBAL_VISUAL_STYLE = "GLOBAL_VISUAL_IDENTITY"
GLOBAL_NEGATIVE = "GLOBAL_NEGATIVE"


def _style_snapshot() -> dict:
    return {
        "visual_identity": {
            "medium": [GLOBAL_VISUAL_STYLE],
            "linework": ["GLOBAL_LINEWORK"],
            "rendering": ["GLOBAL_RENDERING"],
            "palette": ["GLOBAL_PALETTE"],
            "shape_language": ["GLOBAL_SHAPE"],
            "texture": ["GLOBAL_TEXTURE"],
            "editorial_language": ["GLOBAL_EDITORIAL"],
        },
        "entity_styles": {
            "character": {
                "appearance": [
                    "CHARACTER_ENTITY_APPEARANCE",
                ],
                "avoid": ["CHARACTER_ENTITY_AVOID"],
            },
            "location": {
                "appearance": ["LOCATION_ENTITY_APPEARANCE"],
                "avoid": ["LOCATION_ENTITY_AVOID"],
            },
            "prop": {
                "appearance": ["PROP_ENTITY_APPEARANCE"],
                "avoid": ["PROP_ENTITY_AVOID"],
            },
            "recurring_visual_subject": {
                "appearance": ["SUBJECT_ENTITY_APPEARANCE"],
                "avoid": ["SUBJECT_ENTITY_AVOID"],
            },
        },
        "global_negative_constraints": [GLOBAL_NEGATIVE],
    }


def _base_style_bible() -> dict:
    return {
        "visual_style_snapshot": _style_snapshot(),
        "characters": [
            {
                "char_id": "char_001",
                "label": "Guide",
                "description": (
                    "A field doctor holding a spear beside a forest hospital desk."
                ),
                "entity_kind": "human_character",
                "narrative_role": "guide",
                "explicit_visual_traits": [
                    "round glasses",
                    "round glasses",
                    "silver scarf",
                ],
                "identity_markers": ["calm posture"],
                "continuity_notes": [
                    "Scene context continuity note beside a cockpit console."
                ],
                "approval_status": "text_approved",
            }
        ],
        "locations": [
            {
                "loc_id": "loc_001",
                "label": "Harbor Lab",
                "description": "A quiet dockside research room.",
                "location_kind": "interior",
                "explicit_visual_traits": ["wide windows"],
                "identity_markers": ["workbench wall"],
                "continuity_notes": ["Reuse the room's layout."],
                "approval_status": "text_approved",
            }
        ],
        "recurring_props": [
            {
                "prop_id": "prop_001",
                "label": "Signal Compass",
                "description": "A small brass compass with a cracked lens.",
                "prop_kind": "instrument",
                "significance": "navigation",
                "explicit_visual_traits": ["cracked lens"],
                "identity_markers": ["brass ring"],
                "continuity_notes": ["Keep the crack visible."],
                "approval_status": "text_approved",
            }
        ],
        "recurring_visual_subjects": [
            {
                "subject_id": "subject_001",
                "label": "Stress Response",
                "description": "A recurring biological alert-state visualization.",
                "visual_type": "biological process",
                "explicit_visual_traits": ["pulse wave"],
                "continuity_notes": ["Keep the biological process readable."],
                "approval_status": "text_approved",
            }
        ],
        "motifs": [
            {
                "motif_id": "motif_001",
                "label": "Pressure vs Danger",
                "description": "A symbolic contrast between lingering pressure and danger.",
                "approval_status": "text_approved",
            }
        ],
    }


def _style_bible_with_human_group() -> dict:
    style_bible = _base_style_bible()
    style_bible["characters"].append(
        {
            "char_id": "char_002",
            "label": "Support group",
            "description": (
                "Unnamed companions whose role evolves from practical assistance "
                "to redistributed labor and coordinated action."
            ),
            "entity_kind": "human_group",
            "narrative_role": "supporting_social_system",
            "explicit_visual_traits": [
                "several unnamed companions",
                "shared practical prehistoric clothing style",
            ],
            "identity_markers": [
                "group identity matters more than any one individual member",
                "members remain visually distinct rather than cloned",
            ],
            "continuity_notes": [
                "Keep helpers generic unless the script explicitly distinguishes one action."
            ],
            "approval_status": "text_approved",
        }
    )
    return style_bible


def _inventory_by_anchor(style_bible: dict | None = None) -> dict[tuple[str, str], dict]:
    inventory = build_visual_reference_inventory(style_bible or _base_style_bible())
    return {
        (item["anchor_type"], item["anchor_id"]): item
        for item in inventory
    }


def _assert_global_style(subject: dict) -> None:
    assert subject["prompt"].count("GLOBAL VISUAL WORLD / RENDERING CONTRACT") == 1
    for token in (
        GLOBAL_VISUAL_STYLE, "GLOBAL_LINEWORK", "GLOBAL_RENDERING",
        "GLOBAL_PALETTE", "GLOBAL_SHAPE", "GLOBAL_TEXTURE", "GLOBAL_EDITORIAL",
    ):
        assert subject["prompt"].count(token) == 1
    assert subject["negative_prompt"].count(GLOBAL_NEGATIVE) == 1
    assert subject["prompt"].index("GLOBAL VISUAL WORLD") < subject["prompt"].index("Entity style:")


def test_character_prompt_uses_character_entity_style_and_global_style() -> None:
    subject = _inventory_by_anchor()[("character", "char_001")]

    assert "CHARACTER_ENTITY_APPEARANCE" in subject["prompt"]
    assert subject["prompt"].count("CHARACTER_ENTITY_APPEARANCE") == 1
    assert "CHARACTER_ENTITY_AVOID" in subject["negative_prompt"]
    _assert_global_style(subject)

    assert "round glasses" in subject["prompt"]
    assert subject["prompt"].count("round glasses") == 1
    assert "silver scarf" in subject["prompt"]
    assert "calm posture" not in subject["prompt"]
    assert "Identity markers:" not in subject["prompt"]


def test_character_prompt_preserves_description_once_and_scopes_it() -> None:
    subject = _inventory_by_anchor()[("character", "char_001")]
    prompt = subject["prompt"]
    description = "A field doctor holding a spear beside a forest hospital desk."

    assert prompt.count(description) == 1
    assert "holding a spear" in prompt
    assert "forest hospital desk" in prompt
    assert "Use the description only for intrinsic, stable character identity traits" in prompt
    assert prompt.index("Use the description only") > prompt.index(description)
    assert "Scene context continuity note beside a cockpit console" not in prompt


def test_character_prompt_requires_isolated_identity_reference() -> None:
    prompt = _inventory_by_anchor()[("character", "char_001")]["prompt"].lower()
    negative = _inventory_by_anchor()[("character", "char_001")][
        "negative_prompt"
    ].lower()

    for phrase in (
        "exactly one isolated full-body character",
        "head to feet",
        "neutral relaxed standing pose",
        "empty hands",
        "plain light neutral background",
        "no props",
        "furniture",
        "environment",
        "narrative activity",
        "do not infer objects or environments",
    ):
        assert phrase in prompt
    for phrase in (
        "multiple characters",
        "handheld objects",
        "props",
        "weapons",
        "tools",
        "furniture",
        "environment",
        "narrative scene",
        "action pose",
        "cropped body",
    ):
        assert phrase in negative


def test_human_group_prompt_requires_multiple_people_and_not_solo_portrait() -> None:
    subject = _inventory_by_anchor(_style_bible_with_human_group())[("character", "char_002")]
    prompt = subject["prompt"].lower()
    negative = subject["negative_prompt"].lower()

    for phrase in (
        "human-group identity reference",
        "several distinct people visible together",
        "never a single-person portrait",
        "at least two people",
        "three to five",
        "group as a whole is the identity anchor",
        "rather than cloned",
        "plain light neutral background",
    ):
        assert phrase in prompt

    assert "exactly one isolated full-body character" not in prompt
    assert "other characters" not in prompt
    assert "multiple characters" not in negative
    assert "Identity markers:" not in subject["prompt"]
    assert "group identity matters more than any one individual member" not in subject["prompt"]

    for phrase in (
        "single isolated character",
        "solo portrait",
        "single-person composition",
        "duplicate cloned faces",
        "identical repeated people",
        "cropped group members",
    ):
        assert phrase in negative


def test_human_group_keeps_character_entity_style_with_global_style() -> None:
    subject = _inventory_by_anchor(_style_bible_with_human_group())[("character", "char_002")]

    assert "CHARACTER_ENTITY_APPEARANCE" in subject["prompt"]
    assert "CHARACTER_ENTITY_AVOID" in subject["negative_prompt"]
    _assert_global_style(subject)


def test_human_group_branch_does_not_change_individual_character_constraints() -> None:
    inventory = _inventory_by_anchor(_style_bible_with_human_group())
    individual = inventory[("character", "char_001")]
    group = inventory[("character", "char_002")]

    assert "exactly one isolated full-body character" in individual["prompt"].lower()
    assert "multiple characters" in individual["negative_prompt"].lower()
    assert "exactly one isolated full-body character" not in group["prompt"].lower()
    assert "multiple characters" not in group["negative_prompt"].lower()


def test_location_prompt_uses_location_entity_style_only() -> None:
    subject = _inventory_by_anchor()[("location", "loc_001")]

    assert "LOCATION_ENTITY_APPEARANCE" in subject["prompt"]
    assert "LOCATION_ENTITY_AVOID" in subject["negative_prompt"]
    assert "CHARACTER_ENTITY_APPEARANCE" not in subject["prompt"]
    assert "PROP_ENTITY_APPEARANCE" not in subject["prompt"]
    assert "Identity markers:" not in subject["prompt"]
    assert "workbench wall" not in subject["prompt"]
    _assert_global_style(subject)


def test_prop_prompt_uses_prop_entity_style_only() -> None:
    subject = _inventory_by_anchor()[("prop", "prop_001")]

    assert "PROP_ENTITY_APPEARANCE" in subject["prompt"]
    assert "PROP_ENTITY_AVOID" in subject["negative_prompt"]
    assert "CHARACTER_ENTITY_APPEARANCE" not in subject["prompt"]
    assert "LOCATION_ENTITY_APPEARANCE" not in subject["prompt"]
    assert "one isolated physical object" in subject["prompt"]
    assert "Identity markers:" not in subject["prompt"]
    assert "brass ring" not in subject["prompt"]
    _assert_global_style(subject)


def test_recurring_visual_subject_uses_subject_entity_style_and_visual_type() -> None:
    subject = _inventory_by_anchor()[("recurring_visual_subject", "subject_001")]

    assert "SUBJECT_ENTITY_APPEARANCE" in subject["prompt"]
    assert "SUBJECT_ENTITY_AVOID" in subject["negative_prompt"]
    assert "Visual type:\nbiological process" in subject["prompt"]
    assert "CHARACTER_ENTITY_APPEARANCE" not in subject["prompt"]
    assert "LOCATION_ENTITY_APPEARANCE" not in subject["prompt"]
    assert "PROP_ENTITY_APPEARANCE" not in subject["prompt"]
    _assert_global_style(subject)


def test_missing_entity_style_fails_closed() -> None:
    style_bible = _base_style_bible()
    del style_bible["visual_style_snapshot"]["entity_styles"]["recurring_visual_subject"]
    style_bible["characters"] = []
    style_bible["locations"] = []
    style_bible["recurring_props"] = []

    with pytest.raises(AppError) as exc:
        build_visual_reference_inventory(style_bible)
    assert exc.value.error_code == "VISUAL_STYLE_SNAPSHOT_INVALID"


def test_global_style_remains_when_entity_style_is_empty() -> None:
    style_bible = _base_style_bible()
    style_bible["visual_style_snapshot"]["entity_styles"]["character"] = {
        "appearance": [],
        "avoid": [],
    }

    subject = _inventory_by_anchor(style_bible)[("character", "char_001")]

    assert "Entity style:" not in subject["prompt"]
    assert "CHARACTER_ENTITY_APPEARANCE" not in subject["prompt"]
    assert "CHARACTER_ENTITY_AVOID" not in subject["negative_prompt"]
    assert GLOBAL_VISUAL_STYLE in subject["prompt"]
    assert GLOBAL_NEGATIVE in subject["negative_prompt"]


def test_character_isolation_constraints_do_not_leak_to_other_anchor_types() -> None:
    inventory = _inventory_by_anchor()
    for key in (
        ("location", "loc_001"),
        ("prop", "prop_001"),
        ("recurring_visual_subject", "subject_001"),
    ):
        prompt = inventory[key]["prompt"].lower()
        for phrase in (
            "full-body character",
            "head to feet",
            "neutral relaxed standing pose",
            "empty hands",
            "plain light neutral background",
            "character identity reference",
        ):
            assert phrase not in prompt


def test_visual_motifs_do_not_create_reference_inventory_subjects() -> None:
    inventory = build_visual_reference_inventory(_base_style_bible())

    assert {item["anchor_type"] for item in inventory} == {
        "character",
        "location",
        "prop",
        "recurring_visual_subject",
    }
    assert all(item["anchor_id"] != "motif_001" for item in inventory)


def test_prompt_compiler_does_not_mutate_style_bible_input() -> None:
    style_bible = _base_style_bible()
    before = deepcopy(style_bible)

    build_visual_reference_inventory(style_bible)

    assert style_bible == before


@pytest.mark.parametrize(
    ("medium", "linework", "rendering", "negative"),
    [
        ("flat marker", "uneven sparse strokes", "unshaded shapes", "no glossy gradients"),
        ("oil painting", "fine detailed contours", "layered naturalistic light", "no cartoon outlines"),
    ],
)
def test_contrasting_channels_keep_all_reference_types_in_their_own_world(
    medium: str, linework: str, rendering: str, negative: str,
) -> None:
    style_bible = _style_bible_with_human_group()
    identity = style_bible["visual_style_snapshot"]["visual_identity"]
    identity["medium"] = [medium]
    identity["linework"] = [linework]
    identity["rendering"] = [rendering]
    style_bible["visual_style_snapshot"]["global_negative_constraints"] = [negative]

    first = build_visual_reference_inventory(style_bible)
    assert first == build_visual_reference_inventory(style_bible)
    assert len(first) == 5
    for anchor in first:
        assert anchor["prompt"].count(medium) == 1
        assert anchor["prompt"].count(linework) == 1
        assert anchor["prompt"].count(rendering) == 1
        assert anchor["negative_prompt"].count(negative) == 1
        assert anchor["prompt"].index(medium) < anchor["prompt"].index("Description:")


def test_invalid_or_missing_global_snapshot_never_silently_falls_back() -> None:
    for snapshot in (None, {}, {"visual_identity": {}}):
        bible = _base_style_bible()
        bible["visual_style_snapshot"] = snapshot
        with pytest.raises(AppError) as exc:
            build_visual_reference_inventory(bible)
        assert exc.value.error_code == "VISUAL_STYLE_SNAPSHOT_INVALID"


def test_unknown_character_entity_kind_fails_closed_instead_of_using_singular_prompt() -> None:
    style_bible = _base_style_bible()
    style_bible["characters"][0]["entity_kind"] = "group"

    with pytest.raises(ValueError, match="Unsupported character entity_kind 'group'"):
        build_visual_reference_inventory(style_bible)


def test_identity_marker_change_still_changes_source_definition_hash() -> None:
    before = _inventory_by_anchor()[("character", "char_001")]["source_definition_hash"]
    style_bible = _base_style_bible()
    style_bible["characters"][0]["identity_markers"] = ["different internal marker"]
    after = _inventory_by_anchor(style_bible)[("character", "char_001")]["source_definition_hash"]

    assert before != after


def test_zenn_character_entity_style_is_concise_rendering_only() -> None:
    dna = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts" / "examples" / "platform" / "channel_dna_zenn.json").read_text(encoding="utf-8")
    )
    style = dna["entity_styles"]["character"]
    appearance = style["appearance"]
    avoid = style["avoid"]
    joined = "\n".join([*appearance, *avoid]).lower()

    assert len(appearance) <= 6
    assert len(avoid) <= 5
    for phrase in (
        "meme-stick",
        "rough uneven",
        "full-color flat",
        "muted palette",
        "anatomy minimal",
        "never polished",
    ):
        assert phrase in joined
    for forbidden in (
        "script/analysis",
        "scene authority",
        "project-specific",
        "come from channel dna",
    ):
        assert forbidden not in joined
