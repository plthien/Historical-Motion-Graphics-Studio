from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from app.modules.v4.contracts import V4ContractValidator


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def channel_dna_snapshot_sha256(payload: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


def _occurrence(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "quote": entry["quote"],
        "context": entry["context_quote"],
        "resolved_script_position": entry["resolved_script_span"][0],
        "section_id": entry["section_id"],
    }


def _base_entity(entry: dict[str, Any]) -> dict[str, Any]:
    occurrences = [_occurrence(item) for item in entry["occurrences"]]
    first = occurrences[0]
    return {
        "label": entry["label"],
        "description": entry["description"],
        "explicit_visual_traits": deepcopy(entry.get("explicit_visual_traits", [])),
        "continuity_notes": deepcopy(entry.get("continuity_notes", [])),
        "physical_constraints": deepcopy(entry.get("physical_constraints", [])),
        "first_mention_quote": first["quote"],
        "resolved_first_mention_position": first["resolved_script_position"],
        "occurrence_quotes": occurrences,
        "presence_ranges": deepcopy(entry["presence_ranges"]),
        "approval_status": "text_approved",
    }


class StyleBibleDraftBuilderV4:
    def __init__(self) -> None:
        self.validator = V4ContractValidator()

    def build(
        self,
        *,
        project_id: str,
        style_bible_revision_id: str,
        style_bible_input: dict[str, Any],
        channel_dna_snapshot: dict[str, Any],
        channel_dna_snapshot_sha256_value: str,
        continuity_review_artifact_id: str | None = None,
        removed_continuity_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        validated = self.validator.validate(
            "style_bible_input_v1.schema.json",
            deepcopy(style_bible_input),
            error_code="STYLE_BIBLE_DRAFT_INVALID",
        )
        catalog = validated["continuity_catalog"]
        visual_style_snapshot = {
            "visual_identity": deepcopy(channel_dna_snapshot["visual_identity"]),
            "entity_styles": deepcopy(channel_dna_snapshot["entity_styles"]),
            "global_negative_constraints": deepcopy(channel_dna_snapshot["global_negative_constraints"]),
        }

        characters = []
        for source in catalog["characters"]:
            item = _base_entity(source)
            item.update(
                char_id=source["char_id"],
                identity_markers=deepcopy(source["identity_markers"]),
                entity_kind=source["entity_kind"],
                narrative_role=source["narrative_role"],
            )
            characters.append(item)

        locations = []
        for source in catalog["locations"]:
            item = _base_entity(source)
            item.update(
                loc_id=source["loc_id"],
                identity_markers=deepcopy(source["identity_markers"]),
                location_kind=source["location_kind"],
            )
            locations.append(item)

        recurring_props = []
        for source in catalog["recurring_props"]:
            item = _base_entity(source)
            item.update(
                prop_id=source["prop_id"],
                identity_markers=deepcopy(source["identity_markers"]),
                prop_kind=source["prop_kind"],
                significance=source["description"],
            )
            recurring_props.append(item)

        recurring_visual_subjects = []
        for source in catalog["recurring_visual_subjects"]:
            occurrences = [_occurrence(item) for item in source["occurrences"]]
            recurring_visual_subjects.append(
                {
                    "subject_id": source["subject_id"],
                    "label": source["label"],
                    "description": source["description"],
                    "visual_type": source["visual_type"],
                    "explicit_visual_traits": deepcopy(source["explicit_visual_traits"]),
                    "continuity_notes": deepcopy(source["continuity_notes"]),
                    "physical_constraints": deepcopy(source["physical_constraints"]),
                    "first_mention_quote": occurrences[0]["quote"],
                    "resolved_first_mention_position": occurrences[0]["resolved_script_position"],
                    "occurrence_quotes": occurrences,
                    "presence_ranges": deepcopy(source["presence_ranges"]),
                    "approval_status": "text_approved",
                }
            )

        motifs = []
        for source in catalog["visual_motifs"]:
            motifs.append(
                {
                    "motif_id": source["motif_id"],
                    "label": source["label"],
                    "description": source["description"],
                    "occurrences": [
                        {
                            "quote": occurrence["quote"],
                            "resolved_script_position": occurrence["resolved_script_span"][0],
                        }
                        for occurrence in source["occurrences"]
                    ],
                    "approval_status": "text_approved",
                }
            )

        payload = {
            "artifact_type": "project_style_bible",
            "schema_version": "2.0",
            "project_id": project_id,
            "style_bible_revision_id": style_bible_revision_id,
            "parent_style_bible_revision_id": None,
            "revision_operation": "draft_generation",
            "status": "text_approved",
            "lineage": {
                "script_analysis_artifact_id": validated["script_analysis_artifact_id"],
                "source_script_artifact_id": validated["source_script_revision_id"],
                "channel_dna_snapshot_sha256": channel_dna_snapshot_sha256_value,
                "continuity_review_artifact_id": continuity_review_artifact_id,
                "removed_continuity_refs": list(removed_continuity_refs or []),
            },
            "visual_style_snapshot": visual_style_snapshot,
            "characters": characters,
            "locations": locations,
            "recurring_props": recurring_props,
            "recurring_visual_subjects": recurring_visual_subjects,
            "motifs": motifs,
        }
        return self.validator.validate(
            "project_style_bible_v1.schema.json",
            payload,
            error_code="STYLE_BIBLE_DRAFT_INVALID",
        )
