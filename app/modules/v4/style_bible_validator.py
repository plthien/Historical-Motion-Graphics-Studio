from __future__ import annotations

from typing import Any

from app.api.errors import AppError


class StyleBibleSemanticValidatorV4:
    """Validate immutable continuity identities/evidence against Script Analysis."""

    _COLLECTIONS = (
        ("characters", "characters", "char_id"),
        ("locations", "locations", "loc_id"),
        ("recurring_props", "recurring_props", "prop_id"),
        ("recurring_visual_subjects", "recurring_visual_subjects", "subject_id"),
        ("motifs", "visual_motifs", "motif_id"),
    )

    def validate(
        self,
        *,
        style_bible: dict[str, Any],
        script_analysis: dict[str, Any],
        channel_dna: dict[str, Any],
    ) -> None:
        lineage = style_bible["lineage"]
        if lineage["source_script_artifact_id"] != script_analysis["source_script_revision_id"]:
            self._fail("source_script_mismatch")
        expected_snapshot = {
            "visual_identity": channel_dna["visual_identity"],
            "entity_styles": channel_dna["entity_styles"],
            "global_negative_constraints": channel_dna["global_negative_constraints"],
        }
        if style_bible["visual_style_snapshot"] != expected_snapshot:
            self._fail("channel_dna_snapshot_mismatch")

        catalog = script_analysis["continuity_catalog"]
        removed_refs = lineage.get("removed_continuity_refs")
        if not isinstance(removed_refs, list) or len(removed_refs) != len(set(removed_refs)):
            self._fail("continuity_review_invalid")
        removable_ids = {
            str(item[id_key])
            for style_key, analysis_key, id_key in self._COLLECTIONS
            if style_key != "motifs"
            for item in catalog[analysis_key]
        }
        if any(ref not in removable_ids for ref in removed_refs):
            self._fail("continuity_review_ref_invalid")
        removed_set = set(removed_refs)
        for style_key, analysis_key, id_key in self._COLLECTIONS:
            actual = style_bible[style_key]
            expected = catalog[analysis_key]
            if style_key != "motifs":
                expected = [item for item in expected if item[id_key] not in removed_set]
            if [item[id_key] for item in actual] != [item[id_key] for item in expected]:
                self._fail(f"{style_key}_identity_mismatch")
            for style_item, analysis_item in zip(actual, expected, strict=True):
                if style_key == "motifs":
                    actual_occ = [
                        (item["quote"], item["resolved_script_position"])
                        for item in style_item["occurrences"]
                    ]
                else:
                    actual_occ = [
                        (
                            item["section_id"],
                            item["quote"],
                            item["context"],
                            item["resolved_script_position"],
                        )
                        for item in style_item["occurrence_quotes"]
                    ]
                    first = style_item["occurrence_quotes"][0]
                    if (
                        style_item["first_mention_quote"] != first["quote"]
                        or style_item["resolved_first_mention_position"] != first["resolved_script_position"]
                    ):
                        self._fail(f"{style_key}_first_mention_mismatch")
                if style_key == "motifs":
                    expected_occ = [
                        (item["quote"], item["resolved_script_span"][0])
                        for item in analysis_item["occurrences"]
                    ]
                else:
                    expected_occ = [
                        (
                            item["section_id"],
                            item["quote"],
                            item["context_quote"],
                            item["resolved_script_span"][0],
                        )
                        for item in analysis_item["occurrences"]
                    ]
                if actual_occ != expected_occ:
                    self._fail(f"{style_key}_occurrence_mismatch")
                if style_key != "motifs":
                    actual_ranges = [
                        (
                            item["section_id"],
                            item["start_quote"],
                            item["end_quote"],
                            tuple(item["resolved_script_span"]),
                        )
                        for item in style_item["presence_ranges"]
                    ]
                    expected_ranges = [
                        (
                            item["section_id"],
                            item["start_quote"],
                            item["end_quote"],
                            tuple(item["resolved_script_span"]),
                        )
                        for item in analysis_item["presence_ranges"]
                    ]
                    if actual_ranges != expected_ranges:
                        self._fail(f"{style_key}_presence_range_mismatch")

    @staticmethod
    def _fail(cause: str) -> None:
        raise AppError(
            "STYLE_BIBLE_ARTIFACT_INVALID",
            "Stored Style Bible failed deterministic Script Analysis validation.",
            409,
            retryable=False,
            details={"cause": cause},
        )
