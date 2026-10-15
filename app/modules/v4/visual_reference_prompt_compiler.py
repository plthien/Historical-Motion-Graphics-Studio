from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.hashing import canonical_json_hash
from app.modules.v4.visual_style_prompting import (
    compile_visual_identity_module,
    validate_visual_style_snapshot,
)

ANCHOR_COLLECTIONS: tuple[tuple[str, str, str], ...] = (
    ("character", "characters", "char_id"),
    ("location", "locations", "loc_id"),
    ("prop", "recurring_props", "prop_id"),
    ("recurring_visual_subject", "recurring_visual_subjects", "subject_id"),
)

CANONICAL_CHARACTER_ENTITY_KINDS: tuple[str, ...] = (
    "human_character",
    "human_group",
)


def _require_character_entity_kind(value: Any) -> str:
    if value not in CANONICAL_CHARACTER_ENTITY_KINDS:
        raise ValueError(
            "Unsupported character entity_kind "
            f"{value!r}; expected one of {CANONICAL_CHARACTER_ENTITY_KINDS}."
        )
    return str(value)


def _clean_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return compact_values([item for item in value if isinstance(item, str)])


def compact_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_value in values:
        value = raw_value.strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _entity_style(snapshot: dict[str, Any], anchor_type: str) -> tuple[list[str], list[str]]:
    entity_styles = snapshot.get("entity_styles")
    if not isinstance(entity_styles, dict):
        return [], []
    style = entity_styles.get(anchor_type)
    if not isinstance(style, dict):
        return [], []
    return _clean_strings(style.get("appearance")), _clean_strings(style.get("avoid"))


def _append_list_section(parts: list[str], heading: str, values: list[str]) -> None:
    if values:
        parts.append(f"{heading}:\n" + "\n".join(f"- {value}" for value in values))


def _append_text_section(parts: list[str], heading: str, value: str | None) -> None:
    if isinstance(value, str) and value.strip():
        parts.append(f"{heading}:\n{value.strip()}")


CHARACTER_TECHNICAL_EXCLUSIONS: tuple[str, ...] = (
    "multiple characters",
    "handheld objects",
    "props",
    "weapons",
    "tools",
    "furniture",
    "vehicles",
    "animals",
    "scenery",
    "environment",
    "narrative scene",
    "narrative activity",
    "action pose",
    "cropped body",
    "obscured silhouette",
    "text",
    "logo",
    "watermark",
    "collage",
    "multiple variants",
)

CHARACTER_GROUP_TECHNICAL_EXCLUSIONS: tuple[str, ...] = (
    "single isolated character",
    "solo portrait",
    "single-person composition",
    "duplicate cloned faces",
    "identical repeated people",
    "handheld objects",
    "props",
    "weapons",
    "tools",
    "furniture",
    "vehicles",
    "animals",
    "scenery",
    "environment",
    "narrative scene",
    "narrative activity",
    "action pose",
    "cropped group members",
    "obscured silhouettes",
    "text",
    "logo",
    "watermark",
    "collage",
    "multiple variants",
)

LOCATION_TECHNICAL_EXCLUSIONS: tuple[str, ...] = (
    "foreground characters",
    "character portrait",
    "handheld props",
    "narrative action",
    "text",
    "logos",
    "watermarks",
    "collage",
    "multiple variants",
)

PROP_TECHNICAL_EXCLUSIONS: tuple[str, ...] = (
    "hands",
    "characters",
    "furniture",
    "vehicles",
    "scenery",
    "environment",
    "text",
    "logos",
    "watermarks",
    "collage",
    "multiple variants",
)

RECURRING_SUBJECT_TECHNICAL_EXCLUSIONS: tuple[str, ...] = (
    "unrelated characters",
    "unrelated props",
    "unrelated environment",
    "narrative scene",
    "text",
    "logos",
    "watermarks",
    "collage",
    "multiple variants",
)


def _technical_exclusions(
    anchor_type: str,
    *,
    entity_kind: str | None = None,
) -> tuple[str, ...]:
    if anchor_type == "character":
        canonical_kind = _require_character_entity_kind(entity_kind)
        if canonical_kind == "human_group":
            return CHARACTER_GROUP_TECHNICAL_EXCLUSIONS
        return CHARACTER_TECHNICAL_EXCLUSIONS
    return {
        "location": LOCATION_TECHNICAL_EXCLUSIONS,
        "prop": PROP_TECHNICAL_EXCLUSIONS,
        "recurring_visual_subject": RECURRING_SUBJECT_TECHNICAL_EXCLUSIONS,
    }[anchor_type]


def _character_prompt(
    *,
    label: str,
    description: str,
    explicit_visual_traits: list[str],
    appearance: list[str],
) -> str:
    parts = [
        f'Create a reusable character identity reference for "{label}".',
        f"Description:\n{description}",
        (
            "Use the description only for intrinsic, stable character identity traits. "
            "Ignore actions, temporary poses, handheld items, furniture, scenery, "
            "locations, and narrative situations mentioned in the description."
        ),
    ]
    _append_list_section(parts, "Visual traits", explicit_visual_traits)
    _append_list_section(parts, "Entity style", appearance)
    parts.extend(
        [
            (
                "Show exactly one isolated full-body character, centered and visible "
                "from head to feet, in a neutral relaxed standing pose with visible "
                "empty hands and a clear unobstructed silhouette, on a plain light "
                "neutral background."
            ),
            (
                "Keep wearable identity items only when they are stated in the "
                "description or visual traits. Do not infer wearables from role, "
                "profession, era, culture, activity, or story context."
            ),
            (
                "No props, handheld objects, weapons, tools, furniture, vehicles, "
                "animals, scenery, environment, other characters, narrative activity, "
                "text, logos, watermarks, collage, or multiple variants."
            ),
            (
                "Do not infer objects or environments from the character's role, "
                "profession, era, culture, activity, or story context."
            ),
        ]
    )
    return "\n\n".join(parts).strip()


def _character_group_prompt(
    *,
    label: str,
    description: str,
    explicit_visual_traits: list[str],
    appearance: list[str],
) -> str:
    parts = [
        f'Create a reusable human-group identity reference for "{label}".',
        f"Description:\n{description}",
        (
            "Use the description only for intrinsic, stable group identity traits. "
            "Ignore temporary actions, handheld items, furniture, scenery, locations, "
            "and narrative situations mentioned in the description."
        ),
    ]
    _append_list_section(parts, "Visual traits", explicit_visual_traits)
    _append_list_section(parts, "Entity style", appearance)
    parts.extend(
        [
            (
                "If a source visual trait describes a temporary action, object transfer, "
                "or narrative moment, use only its stable group-level implication, such as "
                "the presence of multiple distinct members. Do not depict that action or object."
            ),
            (
                "Show one cohesive small group with several distinct people visible "
                "together, never a single-person portrait. When the source does not "
                "specify a count, show at least two people and preferably three to five. "
                "Keep every member readable from head to feet in neutral relaxed standing "
                "poses on a plain light neutral background. The group as a whole is the "
                "identity anchor; no individual member should dominate as the sole subject."
            ),
            (
                "Keep the members visually distinct rather than cloned: vary faces, body "
                "silhouettes, and small intrinsic details while preserving the approved "
                "shared visual style and group identity. Do not invent names, kinship, "
                "hierarchy, biography, or fixed individual roles that the source does not state."
            ),
            (
                "Keep wearable identity items only when they are stated in the description "
                "or visual traits. Do not infer wearables from role, profession, era, "
                "culture, activity, or story context."
            ),
            (
                "No props, handheld objects, weapons, tools, furniture, vehicles, animals, "
                "scenery, environment, narrative activity, text, logos, watermarks, collage, "
                "or multiple variants."
            ),
        ]
    )
    return "\n\n".join(parts).strip()


def _location_prompt(
    *,
    label: str,
    description: str,
    explicit_visual_traits: list[str],
    appearance: list[str],
) -> str:
    parts = [
        f'Create a reusable location reference for "{label}".',
        f"Description:\n{description}",
    ]
    _append_list_section(parts, "Visual traits", explicit_visual_traits)
    _append_list_section(parts, "Entity style", appearance)
    parts.append(
        "Show a clear, uncluttered location identity preserving stable spatial "
        "anchors, geometry, and setting traits. Its shape, linework, rendering "
        "and finish must follow the global visual world contract, not an "
        "independent environment-art convention. Keep the location itself as "
        "the subject; avoid "
        "foreground characters, narrative action, text, logos, collage, or multiple variants."
    )
    return "\n\n".join(parts).strip()


def _prop_prompt(
    *,
    label: str,
    description: str,
    explicit_visual_traits: list[str],
    appearance: list[str],
) -> str:
    parts = [
        f'Create a reusable prop reference for "{label}".',
        f"Description:\n{description}",
    ]
    _append_list_section(parts, "Visual traits", explicit_visual_traits)
    _append_list_section(parts, "Entity style", appearance)
    parts.append(
        "Show one isolated physical object with a clear unobstructed silhouette on "
        "a simple neutral background. No hands, characters, furniture, scenery, "
        "environment, text, logos, collage, or multiple variants."
    )
    return "\n\n".join(parts).strip()


def _recurring_subject_prompt(
    *,
    label: str,
    description: str,
    visual_type: str | None,
    explicit_visual_traits: list[str],
    appearance: list[str],
) -> str:
    parts = [
        f'Create a reusable recurring visual subject reference for "{label}".',
        f"Description:\n{description}",
    ]
    _append_text_section(parts, "Visual type", visual_type)
    _append_list_section(parts, "Visual traits", explicit_visual_traits)
    _append_list_section(parts, "Entity style", appearance)
    parts.append(
        "Create a clear consistent representation of this subject, entity class, "
        "process, or phenomenon. Keep the representation focused on the described "
        "subject rather than a narrative scene; no text, logos, collage, or multiple variants."
    )
    return "\n\n".join(parts).strip()


def _compile_prompt(
    *,
    anchor_type: str,
    label: str,
    description: str,
    item: dict[str, Any],
    appearance: list[str],
) -> str:
    explicit_visual_traits = _clean_strings(item.get("explicit_visual_traits"))
    if anchor_type == "character":
        entity_kind = _require_character_entity_kind(item.get("entity_kind"))
        prompt_builder = (
            _character_group_prompt
            if entity_kind == "human_group"
            else _character_prompt
        )
        return prompt_builder(
            label=label,
            description=description,
            explicit_visual_traits=explicit_visual_traits,
            appearance=appearance,
        )
    if anchor_type == "location":
        return _location_prompt(
            label=label,
            description=description,
            explicit_visual_traits=explicit_visual_traits,
            appearance=appearance,
        )
    if anchor_type == "prop":
        return _prop_prompt(
            label=label,
            description=description,
            explicit_visual_traits=explicit_visual_traits,
            appearance=appearance,
        )
    return _recurring_subject_prompt(
        label=label,
        description=description,
        visual_type=item.get("visual_type") if isinstance(item.get("visual_type"), str) else None,
        explicit_visual_traits=explicit_visual_traits,
        appearance=appearance,
    )


def _definition(anchor_type: str, item: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "character": ("char_id", "label", "description", "entity_kind", "narrative_role", "explicit_visual_traits", "identity_markers", "continuity_notes"),
        "location": ("loc_id", "label", "description", "location_kind", "explicit_visual_traits", "identity_markers", "continuity_notes"),
        "prop": ("prop_id", "label", "description", "prop_kind", "significance", "explicit_visual_traits", "identity_markers", "continuity_notes"),
        "recurring_visual_subject": ("subject_id", "label", "description", "visual_type", "explicit_visual_traits", "continuity_notes"),
    }[anchor_type]
    return {key: deepcopy(item.get(key)) for key in allowed}


def source_definition_hash(anchor_type: str, item: dict[str, Any]) -> str:
    return "sha256:" + canonical_json_hash({"anchor_type": anchor_type, "definition": _definition(anchor_type, item)})


def build_visual_reference_inventory(style_bible: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = validate_visual_style_snapshot(style_bible.get("visual_style_snapshot"))
    global_style = compile_visual_identity_module(snapshot)
    global_negatives = snapshot["global_negative_constraints"]
    inventory: list[dict[str, Any]] = []
    for anchor_type, collection_name, id_field in ANCHOR_COLLECTIONS:
        collection = style_bible.get(collection_name, [])
        if not isinstance(collection, list):
            continue
        for raw in collection:
            if not isinstance(raw, dict) or raw.get("approval_status") != "text_approved":
                continue
            anchor_id = raw.get(id_field)
            label = raw.get("label")
            description = raw.get("description")
            if not all(isinstance(value, str) and value.strip() for value in (anchor_id, label, description)):
                continue
            clean_label = label.strip()
            clean_description = description.strip()
            appearance, avoid = _entity_style(snapshot, anchor_type)
            inventory.append(
                {
                    "anchor_type": anchor_type,
                    "anchor_id": anchor_id,
                    "label": clean_label,
                    "source_definition_hash": source_definition_hash(anchor_type, raw),
                    "prompt": (
                        "GLOBAL VISUAL WORLD / RENDERING CONTRACT\n"
                        "Apply this approved visual grammar to every structural mark "
                        "of this reference; entity appearance below is subordinate.\n"
                        f"{global_style}\n\n"
                    ) + _compile_prompt(
                        anchor_type=anchor_type,
                        label=clean_label,
                        description=clean_description,
                        item=raw,
                        appearance=appearance,
                    ),
                    "negative_prompt": ", ".join(
                        compact_values(
                            [
                                *global_negatives,
                                *avoid,
                                *_technical_exclusions(
                                    anchor_type,
                                    entity_kind=(
                                        raw.get("entity_kind")
                                        if isinstance(raw.get("entity_kind"), str)
                                        else None
                                    ),
                                ),
                            ]
                        )
                    ),
                }
            )
    inventory.sort(key=lambda item: (item["anchor_type"], item["anchor_id"]))
    return inventory
