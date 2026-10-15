from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Callable

from pydantic import ValidationError

from app.api.prompt_composer_schemas_v4 import PromptBundleModel, PromptComposerInput
from app.core.hashing import canonical_json_hash
from app.modules.v4.prompt_bundle_validator import validate_prompt_bundle
from app.modules.v4.prompt_composer_input_builder import PromptComposerError
from app.modules.v4.recurring_visual_subject_semantics import resolve_recurring_subject_kind
from app.modules.v4.visual_style_prompting import (
    compile_global_negative_constraints,
    compile_visual_identity_module,
    entity_negative_constraints,
    merge_negative_constraints,
)



_NO_BEAT_OVERRIDE_MARKERS = {
    "No beat-specific lighting override.",
    "No beat-specific color override.",
}


def _beat_specific_override(value: Any) -> str | None:
    if not isinstance(value, str) or value in _NO_BEAT_OVERRIDE_MARKERS:
        return None
    stripped = value.strip()
    return stripped or None

_BEAT_STRUCTURAL_NEGATIVES = (
    "multiple images",
    "multiple panels",
    "title",
    "letters",
    "words",
    "numbers",
    "labels",
    "captions",
    "annotations",
    "watermark",
    "signature",
    "logo",
    "interface",
)
_BEAT_STRUCTURAL_NEGATIVES_WITH_APPROVED_TEXT = (
    "multiple images",
    "multiple panels",
    "unapproved text",
    "extra text",
    "extra labels",
    "watermark",
    "signature",
    "logo",
    "interface",
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _dedupe_fragments(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = _normalize(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _entity_maps(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], ...]:
    entities = payload["style_entities"]
    return (
        {item["char_id"]: item for item in entities["characters"]},
        {item["loc_id"]: item for item in entities["locations"]},
        {item["prop_id"]: item for item in entities["recurring_props"]},
        {item["subject_id"]: item for item in entities["recurring_visual_subjects"]},
        {item["motif_id"]: item for item in entities["motifs"]},
    )


def _beat_has_living_recurring_subject(
    beat: dict[str, Any],
    recurring_subjects: dict[str, dict[str, Any]],
) -> bool:
    return any(
        resolve_recurring_subject_kind(str(recurring_subjects[ref].get("visual_type") or ""))
        in {"living_individual", "living_group"}
        for ref in beat["selected_recurring_visual_subject_refs"]
    )


def _entity_label(item: dict[str, Any], fallback: str) -> str:
    value = _normalize(str(item.get("label") or ""))
    return value or fallback


def _meaningful_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = _normalize(value.replace("_", " "))
    return text or None


def _sentence(value: str) -> str:
    text = _normalize(value)
    if not text:
        return text
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _block(title: str, values: list[str | None]) -> str | None:
    lines = [_sentence(value) for value in _dedupe_fragments(values) if value]
    return f"{title}\n\n" + "\n".join(lines) if lines else None


def _reference_character(anchor_id: str, entity: dict[str, Any]) -> str:
    label = _entity_label(entity, anchor_id)
    return (
        f'Use @{anchor_id} as the exact visual identity authority for "{label}". '
        "Preserve the visible face, hair, body proportions, garment, silhouette, "
        "and stable identity traits shown in the reference. Pose, expression, "
        "camera angle, and action may change for this beat; identity must not."
    )


def _reference_location(anchor_id: str, entity: dict[str, Any]) -> str:
    label = _entity_label(entity, anchor_id)
    return (
        f'Use @{anchor_id} only as the stable location-identity reference for "{label}". '
        "Retain its recognizable spatial anchors, environmental character and identity geometry, "
        "but render them using the current global visual style; do not inherit incompatible "
        "reference polish, texture, lighting or detail level. Build a completely new environment "
        "composition for this beat. The current SCENE and CAMERA AND COMPOSITION instructions "
        "determine which environmental elements are visible, where they appear, and how the shot "
        f"is arranged. Do not use @{anchor_id} as a background, base image, scene template, "
        "composition template, or source canvas. Do not reproduce its object inventory, object "
        "positions, crop, camera angle, framing, or layout. Include only environmental elements "
        f"relevant to the current beat. @{anchor_id} is a soft location-identity reference, "
        "not a background, base canvas, scene template, or composition authority."
    )


def _reference_prop(anchor_id: str, entity: dict[str, Any]) -> str:
    label = _entity_label(entity, anchor_id)
    return (
        f'Use @{anchor_id} as the exact visual identity authority for "{label}". '
        "Preserve its recognizable shape, proportions, materials, colors, and stable "
        "design traits. Orientation, placement, apparent size in frame, and interaction "
        "may change with camera and framing, but intrinsic proportions, real-world relative "
        "scale, graspability, and supplied physical constraints must remain valid."
    )


def _reference_recurring_subject(anchor_id: str, entity: dict[str, Any]) -> str:
    label = _entity_label(entity, anchor_id)
    return (
        f'Use @{anchor_id} as the visual identity authority for "{label}". '
        "Preserve its stable visible identity and recurring design. Adapt pose, "
        "state, and framing only as required by this beat."
    )


REFERENCE_AUTHORITY_COMPILERS: dict[str, Callable[[str, dict[str, Any]], str]] = {
    "character": _reference_character,
    "location": _reference_location,
    "prop": _reference_prop,
    "recurring_visual_subject": _reference_recurring_subject,
}


def compile_reference_authority(anchor_type: str, anchor_id: str, entity_metadata: dict[str, Any]) -> str:
    compiler = REFERENCE_AUTHORITY_COMPILERS.get(anchor_type)
    if compiler is None:
        raise PromptComposerError(
            "PROMPT_COMPOSER_UNKNOWN_REFERENCE_ANCHOR_TYPE",
            "Prompt Composer cannot safely compile an unknown reference anchor type.",
            details={"anchor_type": anchor_type, "anchor_id": anchor_id},
        )
    return compiler(anchor_id, entity_metadata)


def _reference_entity(
    anchor_type: str,
    anchor_id: str,
    *,
    characters: dict[str, dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    props: dict[str, dict[str, Any]],
    recurring_subjects: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    maps = {
        "character": characters,
        "location": locations,
        "prop": props,
        "recurring_visual_subject": recurring_subjects,
    }
    if anchor_type not in maps:
        raise PromptComposerError(
            "PROMPT_COMPOSER_UNKNOWN_REFERENCE_ANCHOR_TYPE",
            "Prompt Composer cannot safely compile an unknown reference anchor type.",
            details={"anchor_type": anchor_type, "anchor_id": anchor_id},
        )
    try:
        return maps[anchor_type][anchor_id]
    except KeyError as exc:
        raise PromptComposerError(
            "PROMPT_COMPOSER_REFERENCE_METADATA_MISSING",
            "Prompt Composer reference metadata is missing.",
            details={"anchor_type": anchor_type, "anchor_id": anchor_id},
        ) from exc


def _approved_reference_semantics(
    beat: dict[str, Any],
    *,
    characters: dict[str, dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    props: dict[str, dict[str, Any]],
    recurring_subjects: dict[str, dict[str, Any]],
) -> list[str]:
    result: list[str] = []
    for binding in beat["selected_reference_bindings"]:
        anchor_type = binding["anchor_type"]
        anchor_id = binding["anchor_id"]
        metadata = _reference_entity(
            anchor_type,
            anchor_id,
            characters=characters,
            locations=locations,
            props=props,
            recurring_subjects=recurring_subjects,
        )
        result.append(compile_reference_authority(anchor_type, anchor_id, metadata))
    return result


def _unreferenced_semantic_entity(item: dict[str, Any], *, label: str) -> str | None:
    fragments = _dedupe_fragments(
        [str(value) for value in (item.get("explicit_visual_traits") or [])]
        + [str(value) for value in (item.get("identity_markers") or [])]
    )
    if not fragments:
        description = _meaningful_text(item.get("description"))
        fragments = [description] if description else []
    if not fragments:
        return None
    return f'Use compact approved text metadata for unreferenced {label} "{_entity_label(item, label)}": {"; ".join(fragments)}.'


def _unreferenced_entity_semantics(
    beat: dict[str, Any],
    *,
    characters: dict[str, dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    props: dict[str, dict[str, Any]],
    recurring_subjects: dict[str, dict[str, Any]],
) -> list[str]:
    referenced = {(binding["anchor_type"], binding["anchor_id"]) for binding in beat["selected_reference_bindings"]}
    result: list[str] = []
    for ref in beat["selected_character_refs"]:
        if ("character", ref) not in referenced:
            result.append(_unreferenced_semantic_entity(characters[ref], label="character"))
    for ref in beat["selected_location_refs"]:
        if ("location", ref) not in referenced:
            result.append(_unreferenced_semantic_entity(locations[ref], label="location"))
    for ref in beat["selected_prop_refs"]:
        if ("prop", ref) not in referenced:
            result.append(_unreferenced_semantic_entity(props[ref], label="prop"))
    for ref in beat["selected_recurring_visual_subject_refs"]:
        if ("recurring_visual_subject", ref) not in referenced:
            result.append(_unreferenced_semantic_entity(recurring_subjects[ref], label="recurring visual subject"))
    return [value for value in result if value]


def _output_contract(beat: dict[str, Any], options: dict[str, Any]) -> str:
    aspect_ratio = str(options["aspect_ratio"])
    approved_text = _dedupe_fragments([str(value) for value in beat["visible_text"]])
    base = (
        f"Create exactly one native {aspect_ratio} image for this approved beat. "
        "Generate a completely new full-frame composition from the SCENE and CAMERA AND "
        "COMPOSITION instructions below. Use the full canvas edge-to-edge. "
        "Reference images are identity/design sources only. Do not place, embed, extend, "
        "trace, reproduce, or use any reference image as the background, base canvas, inset "
        "image, or scene template. "
    )
    if not approved_text:
        return (
            base
            + "Do not create a collage, split screen, filler bars, letterboxing, "
            "pillarboxing, text, captions, labels, watermark, logo, signature, or "
            "interface elements."
        )
    return (
        base
        + f"Visible text is explicitly approved and must be exactly: {'; '.join(approved_text)}. "
        "Do not create a collage, split screen, filler bars, letterboxing, pillarboxing, "
        "unapproved text, captions, labels, watermark, logo, signature, or interface elements."
    )


def _continuity_instruction(beat: dict[str, Any]) -> str | None:
    if not beat["previous_beat_image_reference_required"]:
        return None
    relation = str(beat.get("continuity_with_previous") or "")
    mapping = {
        "same_setup": "Preserve the ongoing visible subject identities, environment/location identity, and basic spatial setup shown in @prev_beat, except where this beat explicitly changes them.",
        "match_subject": "Use @prev_beat only to preserve the relevant subject, object, or character identity and ongoing visible state. Do not carry unrelated location/environment or prop content from the previous Beat.",
        "match_location": "Use @prev_beat only to preserve the relevant environment/location identity and spatial context. Do not carry unrelated characters, subjects, or props from the previous Beat.",
        "match_composition": "Use @prev_beat only as a geometry reference for framing, placement, scale, negative space, and spatial arrangement. Do not copy prior physical subjects, characters, locations, or props merely because they appear in @prev_beat; current selected references and current Beat instructions determine what is present.",
        "graphic_sequence": "Use @prev_beat only for editorial sequence and layout grammar such as typography, panel/grid structure, ordering, labels, or graphic motifs. Do not carry physical characters, subjects, environments, or props from the previous Beat unless the current Beat selects or instructs them.",
    }
    instruction = mapping.get(relation)
    if instruction is None:
        return None
    return (
        "Use @prev_beat as the approved immediately previous-beat continuity reference. "
        "Preserve only the transient visual continuity required by this relation. Apply only the "
        "relation-specific continuity scope below; do not broaden it into a generic instruction to "
        "copy the previous image. "
        f"{instruction} "
        "Canonical character and location references remain the authority for intrinsic identity. "
        "Current beat instructions remain authoritative for intentional changes. Previous-render "
        "carry must never import a setting, period, evidence status, or world cue that conflicts "
        "with the current VISUAL CONTEXT."
    )


def _visual_context_instruction(beat: dict[str, Any], payload: dict[str, Any]) -> str:
    context_ref = str(beat["visual_context_ref"])
    context = payload["visual_context_catalog"][context_ref]
    values = [
        f"Context ref: {context_ref}",
        f"Mode: {str(context['mode']).replace('_', ' ')}",
        f"Timeframe: {context['timeframe']}" if context.get("timeframe") else None,
        f"Setting: {context['setting']}" if context.get("setting") else None,
        *[f"Constraint: {value}" for value in context.get("constraints", [])],
        (
            "The current visual context is semantic and provenance authority. Do not replace it "
            "with an analogous unrelated setting, period, evidence status, or modern or historical "
            "world. Current context outranks incompatible cues from the previous rendered Beat."
        ),
    ]
    return " ".join(_sentence(value) for value in values if value)


def _physical_constraints_block(
    beat: dict[str, Any],
    *,
    characters: dict[str, dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    props: dict[str, dict[str, Any]],
    recurring_subjects: dict[str, dict[str, Any]],
) -> str | None:
    entries: list[str] = []
    for anchor_type, refs, entities in (
        ("character", beat["selected_character_refs"], characters),
        ("location", beat["selected_location_refs"], locations),
        ("prop", beat["selected_prop_refs"], props),
        (
            "recurring visual subject",
            beat["selected_recurring_visual_subject_refs"],
            recurring_subjects,
        ),
    ):
        for ref in refs:
            entity = entities[ref]
            constraints = _dedupe_fragments(entity.get("physical_constraints", []))
            if not constraints:
                continue
            label = _entity_label(entity, ref).replace('"', "'")
            lines = [
                f'For {anchor_type} {ref} "{label}":',
                *[f"- {_sentence(constraint)}" for constraint in constraints],
            ]
            entries.append("\n".join(lines))
    if not entries:
        return None
    return "PHYSICAL CONSTRAINTS\n\n" + "\n\n".join(entries)


def _continuity_reference_requirement(
    beat: dict[str, Any],
    previous_beat: dict[str, Any] | None,
) -> dict[str, str] | None:
    if not beat["previous_beat_image_reference_required"]:
        return None
    relation = str(beat.get("continuity_with_previous") or "")
    if previous_beat is None:
        raise PromptComposerError(
            "PROMPT_COMPOSER_CONTINUITY_SOURCE_INVALID",
            "Beats requiring the previous rendered image need an immediately previous Beat in global Beat order.",
            details={"beat_id": beat.get("beat_id"), "continuity_relation": relation},
        )
    return {
        "reference_tag": "prev_beat",
        "source_beat_id": str(previous_beat["beat_id"]),
        "relation": relation,
    }


def _style_block(snapshot: dict[str, Any], beat: dict[str, Any], recurring_subjects: dict[str, dict[str, Any]]) -> str:
    style = compile_visual_identity_module(snapshot)
    referenced = {(binding["anchor_type"], binding["anchor_id"]) for binding in beat["selected_reference_bindings"]}
    has_unreferenced_living_subject = any(
        ("recurring_visual_subject", ref) not in referenced
        and resolve_recurring_subject_kind(str(recurring_subjects[ref].get("visual_type") or ""))
        in {"living_individual", "living_group"}
        for ref in beat["selected_recurring_visual_subject_refs"]
    )
    if any(("character", ref) not in referenced for ref in beat["selected_character_refs"]) or has_unreferenced_living_subject:
        style = f"{style} Keep unreferenced secondary characters in the same approved character visual language as the referenced subjects."
    return style


_STRUCTURAL_NEGATIVE_NORMALIZED = {
    _normalize(value).lower()
    for value in (
        *_BEAT_STRUCTURAL_NEGATIVES,
        *_BEAT_STRUCTURAL_NEGATIVES_WITH_APPROVED_TEXT,
        "watermarks",
        "signatures",
        "logos",
        "interface elements",
        "text",
        "captions",
    )
}


def _negative_render_terms(value: str) -> list[str]:
    lowered = value.lower()
    terms: list[str] = []
    if "photoreal" in lowered:
        terms.append("photorealistic")
    if "semi-realistic" in lowered or "semi realistic" in lowered:
        terms.append("semi-realistic")
    if "painterly" in lowered:
        terms.append("painterly")
    if "documentary" in lowered:
        terms.append("documentary")
    return _dedupe_fragments(terms)


def _compact_negative_constraints(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    render_terms: list[str] = []
    for value in values:
        normalized = _normalize(value)
        if not normalized:
            continue
        lowered = normalized.lower().rstrip(".")
        if lowered in _STRUCTURAL_NEGATIVE_NORMALIZED:
            continue
        terms = _negative_render_terms(normalized)
        if terms:
            render_terms = _dedupe_fragments([*render_terms, *terms])
            continue
        if lowered not in seen:
            seen.add(lowered)
            result.append(normalized)
    if render_terms:
        result.insert(0, f"No {', '.join(render_terms)} rendering.")
    return result or ["No off-brief visual style or unsupported identity changes."]


def compose_image_prompt(beat: dict[str, Any], payload: dict[str, Any]) -> str:
    characters, locations, props, recurring_subjects, _ = _entity_maps(payload)
    snapshot = payload["visual_style_snapshot"]
    approved_reference_semantics = _approved_reference_semantics(
        beat,
        characters=characters,
        locations=locations,
        props=props,
        recurring_subjects=recurring_subjects,
    )
    reference_lines = [
        *approved_reference_semantics,
        *_unreferenced_entity_semantics(
            beat,
            characters=characters,
            locations=locations,
            props=props,
            recurring_subjects=recurring_subjects,
        ),
    ]
    physical_constraints_block = _physical_constraints_block(
        beat,
        characters=characters,
        locations=locations,
        props=props,
        recurring_subjects=recurring_subjects,
    )
    blocks = [
        _block("OUTPUT CONTRACT", [_output_contract(beat, payload["options"])]),
        _block("VISUAL CONTEXT", [_visual_context_instruction(beat, payload)]),
        _block("STYLE AND REFERENCE PRECEDENCE", [
            "The locked current Beat and visual context determine what exists and happens; "
            "do not invent narrative facts. The global Style Bible controls the whole frame's "
            "medium, linework, shape language, rendering, palette, texture and editorial grammar. "
            "Canonical references preserve only their stable identity, color and geometry within "
            "that global style; never copy conflicting render polish or accidental reference "
            "artifacts. The current scene and camera determine action, crop and arrangement; "
            "a location reference is not a background canvas. A previous Beat reference carries "
            "only the explicitly requested transient continuity, never style or previous errors. "
            "Use one coherent mark-making and finish level across every included figure, animal, "
            "object and environment. Express only the current Beat's authorized spatial and "
            "physical relationships; do not add actors, objects or interactions to holds, "
            "editorial diagrams or environment-only images."
        ]),
        _block("SCENE", [beat["visual_concept"], beat["subject_action"], beat["environment_emphasis"]]),
        _block("REFERENCE AUTHORITY", reference_lines),
        physical_constraints_block,
        _block(
            "CAMERA AND COMPOSITION",
            [
                f"Framing: {beat['framing'].replace('_', ' ')}",
                f"Camera angle: {beat['camera_angle'].replace('_', ' ')}",
                f"Composition: {beat['composition']}",
                "Create a single still-image composition, not a storyboard panel",
            ],
        ),
        _block("CONTINUITY", [_continuity_instruction(beat)]),
        _block(
            "LIGHTING AND COLOR",
            [
                f"Lighting: {value}" if (value := _beat_specific_override(beat["lighting_direction"])) else None,
                f"Color emphasis: {value}" if (value := _beat_specific_override(beat["color_emphasis"])) else None,
            ],
        ),
        _block("STYLE", [_style_block(snapshot, beat, recurring_subjects)]),
    ]
    return "\n\n".join(block for block in blocks if block)


def compose_negative_prompt(beat: dict[str, Any], payload: dict[str, Any]) -> str:
    snapshot = payload["visual_style_snapshot"]
    _, _, _, recurring_subjects, _ = _entity_maps(payload)
    has_living_recurring = _beat_has_living_recurring_subject(beat, recurring_subjects)
    groups: list[list[str] | tuple[str, ...]] = [compile_global_negative_constraints(snapshot)]
    if beat["selected_character_refs"] or has_living_recurring:
        groups.append(entity_negative_constraints(snapshot, "character"))
    if beat["selected_location_refs"]:
        groups.append(entity_negative_constraints(snapshot, "location"))
    if beat["selected_prop_refs"]:
        groups.append(entity_negative_constraints(snapshot, "prop"))
    if beat["selected_recurring_visual_subject_refs"]:
        groups.append(entity_negative_constraints(snapshot, "recurring_visual_subject"))
    groups.append(beat["negative_requirements"])
    groups.append(
        _BEAT_STRUCTURAL_NEGATIVES_WITH_APPROVED_TEXT
        if beat["visible_text"]
        else _BEAT_STRUCTURAL_NEGATIVES
    )
    return "; ".join(_compact_negative_constraints(merge_negative_constraints(*groups)))


def compute_prompt_hash(
    *,
    image_prompt: str,
    negative_prompt: str,
    generation_options: dict[str, Any],
    reference_bindings: list[dict[str, Any]],
) -> str:
    return "sha256:" + canonical_json_hash(
        {
            "image_prompt": image_prompt,
            "negative_prompt": negative_prompt,
            "generation_settings_snapshot": generation_options,
            "reference_binding_snapshot": reference_bindings,
        }
    )


def _compile_prompt_bundle(
    *,
    prompt_composer_input: dict[str, Any],
    item_id_allocator: Callable[[int, dict[str, Any]], str] | None = None,
    parent_prompt_bundle_revision_id: str | None = None,
    revision_operation: str = "full_compilation",
    status: str = "draft_ready",
    operation_target_beat_id: str | None = None,
) -> dict[str, Any]:
    try:
        parsed = PromptComposerInput.model_validate(deepcopy(prompt_composer_input))
    except ValidationError as exc:
        raise PromptComposerError("PROMPT_COMPOSER_INPUT_INVALID", "Prompt Composer input is invalid.") from exc
    payload = parsed.model_dump(mode="json")
    allocator = item_id_allocator or (lambda index, _: f"prompt_{index:03d}")
    prompt_items: list[dict[str, Any]] = []
    previous_beat: dict[str, Any] | None = None
    for index, beat in enumerate(payload["visual_beats"], start=1):
        image_prompt = compose_image_prompt(beat, payload)
        negative_prompt = compose_negative_prompt(beat, payload)
        generation_options = deepcopy(payload["options"])
        reference_bindings = deepcopy(beat["selected_reference_bindings"])
        continuity_requirement = _continuity_reference_requirement(beat, previous_beat)
        prompt_items.append({
            "prompt_item_id": allocator(index, deepcopy(beat)),
            "scene_id": beat["scene_id"],
            "beat_id": beat["beat_id"],
            "beat_order": beat["beat_order"],
            "local_beat_order": beat["local_beat_order"],
            "continuity_group_id": beat["continuity_group_id"],
            "visual_priority": beat["visual_priority"],
            "target_asset_role": "beat_visual",
            "prompt_origin": "compiled",
            "image_prompt": image_prompt,
            "negative_prompt": negative_prompt,
            "prompt_hash": compute_prompt_hash(
                image_prompt=image_prompt,
                negative_prompt=negative_prompt,
                generation_options=generation_options,
                reference_bindings=reference_bindings,
            ),
            "generation_options": generation_options,
            "reference_bindings": reference_bindings,
            "continuity_reference_requirement": deepcopy(continuity_requirement),
            "source_provenance": {
                "representation_family": beat["representation_family"],
                "selected_character_refs": deepcopy(beat["selected_character_refs"]),
                "selected_location_refs": deepcopy(beat["selected_location_refs"]),
                "selected_prop_refs": deepcopy(beat["selected_prop_refs"]),
                "selected_recurring_visual_subject_refs": deepcopy(beat["selected_recurring_visual_subject_refs"]),
                "selected_motif_refs": deepcopy(beat["selected_motif_refs"]),
                "selected_native_visual_reference_ids": deepcopy(beat["selected_native_visual_reference_ids"]),
            },
        })
        previous_beat = beat
    bundle = {
        "artifact_type": "prompt_bundle",
        "schema_version": "3.0",
        "project_id": payload["project_id"],
        "prompt_bundle_revision_id": payload["prompt_bundle_revision_id"],
        "parent_prompt_bundle_revision_id": parent_prompt_bundle_revision_id,
        "revision_operation": revision_operation,
        "operation_target_beat_id": operation_target_beat_id,
        "source_script_revision_id": payload["source_script_revision_id"],
        "source_script_analysis_revision_id": payload["source_script_analysis_revision_id"],
        "source_style_bible_revision_id": payload["source_style_bible_revision_id"],
        "source_scene_plan_revision_id": payload["source_scene_plan_revision_id"],
        "source_visual_plan_revision_id": payload["source_visual_plan_revision_id"],
        "source_visual_reference_approval_set_artifact_id": payload[
            "source_visual_reference_approval_set_artifact_id"
        ],
        "status": status,
        "options": deepcopy(payload["options"]),
        "source_beat_count": len(prompt_items),
        "prompt_item_count": len(prompt_items),
        "prompt_items": prompt_items,
        "backend_qc": {},
    }
    report = validate_prompt_bundle(prompt_composer_input=payload, prompt_bundle=bundle, allow_missing_backend_qc=True)
    bundle["backend_qc"] = report
    try:
        return PromptBundleModel.model_validate(bundle).model_dump(mode="json")
    except ValidationError as exc:
        raise PromptComposerError("PROMPT_BUNDLE_INVALID", "Compiled Prompt Bundle is invalid.") from exc


def compile_prompt_bundle(
    *,
    prompt_composer_input: dict[str, Any],
    item_id_allocator: Callable[[int, dict[str, Any]], str] | None = None,
    parent_prompt_bundle_revision_id: str | None = None,
    revision_operation: str = "full_compilation",
    status: str = "draft_ready",
    operation_target_beat_id: str | None = None,
) -> dict[str, Any]:
    try:
        return _compile_prompt_bundle(
            prompt_composer_input=prompt_composer_input,
            item_id_allocator=item_id_allocator,
            parent_prompt_bundle_revision_id=parent_prompt_bundle_revision_id,
            revision_operation=revision_operation,
            status=status,
            operation_target_beat_id=operation_target_beat_id,
        )
    except PromptComposerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PromptComposerError(
            "PROMPT_COMPOSER_COMPILATION_FAILED",
            "Prompt Bundle compilation failed safely.",
        ) from exc
