# Combined Script & Analysis External Authoring

Script and Script Analysis are one authoring workspace, one prompt, one ChatGPT response, and one atomic import.

When a Topic is selected, its recommended `pov_style`, `avoid`, and `recommended_pacing_mode` values initialize the editable Script form. They are copied once per selected idea so later user edits are not overwritten. The user may override the suggested `fast | medium | slow` video pacing mode before building the Script prompt; the backend resolves that mode deterministically against the Project's snapshotted Channel DNA `pacing_profiles`.

The user selects one section-planning mode:

- `llm_decides`: ChatGPT chooses the section count and roles within duration-aware backend bounds. The backend computes a recommended count from requested runtime and exposes a bounded range around that recommendation.
- `backend_plan`: the backend supplies an exact section count and ordered roles; ChatGPT must follow them exactly.

The prompt requires ChatGPT to:

1. use its own web search when factual verification is needed;
2. write the complete Script first;
3. freeze the completed Script;
4. analyze only that exact Script;
5. reference returned sections as `sec_001`, `sec_002`, and so on;
6. return one strict JSON object containing `script` and `analysis`;
7. attach/export the result as `script_and_analysis.json`, with pure JSON response fallback.

The backend validates and normalizes the raw Script first, assigns canonical section IDs, rebuilds full narration and runtime diagnostics, then resolves Analysis quotes/entities against that same in-memory canonical Script. Every returned section must declare `planned_narration_wpm` inside broad defensive bounds. Canonical word count is informational; estimated runtime is the sum of `section_word_count / section_planned_narration_wpm`, and the backend derives the whole-Script `planned_average_wpm` only from that aggregate planned duration. Runtime delta remains diagnostic rather than a creative import gate. Script and Analysis are staged and published together with one CAS operation. Structural/semantic failure, pacing-profile lineage mismatch, or stale ownership publishes neither artifact. Downstream stages retain defensive persisted-Script integrity checks.

There is no Research & Generate flow, standalone Script generation, standalone Analysis generation, or section mutation route.
## Canonical continuity scope

For recurring `characters`, `locations`, `recurring_props`, and `recurring_visual_subjects`, the Analysis output separates direct evidence from semantic scope. `occurrences` are representative verbatim anchors. Required `presence_ranges` are exact quote-bounded spans of the frozen Script where the same canonical identity remains narratively active, including pronouns, shorthand, and implicit continuation. `visual_motifs` remain occurrence-only.

The external author must split ranges at generic, comparative, evidentiary, developmental, or different-identity detours rather than treating similar nouns or an entire section as one identity. The backend resolves exact unique start/end quotes to immutable Script spans, rejects malformed/ambiguous/overlapping ranges, and requires each direct occurrence to be contained by a range. Style Bible preserves this provenance; Scene Breakdown consumes it rather than re-inferring identity scope.

## Narration identity authority

The Project carries the exact Channel DNA snapshot taken at project creation. Script & Analysis prompt input includes the frozen `narration_identity` from that snapshot. Its values are free-form channel creative intent; the backend does not map them to `entertaining`, `documentary`, `midnight`, `casefile`, humor levels, factual/entertaining ratios, or any other closed creative taxonomy.

External Script authoring privately resolves a narration delivery plan from the supplied `voice`, `delivery`, `explanation_style`, `tone_boundaries`, and `avoid` fields. A mandatory per-section Narration Delivery Fit Gate runs before Script freeze so evidence-heavy, uncertainty-heavy, climax, and resolution sections do not silently drift into a generic voice. Major abstract/technical/evidence points are also checked for useful ordinary-human translation when that identity calls for practical experiential explanation. Style must never fabricate historical/scientific facts, dialogue, motives, actions, artifacts, or certainty. Ratios or named reference archetypes that appear in Channel DNA/SKILL are editorial guidance only, never backend quotas.

Script Analysis does not output a machine narration-style classification. It analyzes the exact frozen Script under the existing semantic contract.

## Section pacing density authority

Requested `target_duration_minutes` is the runtime authority. Before writing prose, the external Script author plans every section's visual density, ordered state progression, and `planned_narration_wpm`; the full section WPM plan is locked before drafting and cannot be changed afterward merely to make an underwritten or overwritten draft appear to fit runtime. The backend estimates runtime from section-local WPM and derives the aggregate Script WPM diagnostically. There is no fixed 155 WPM Script authority.

The user-selected video `pacing_mode` is separate from section density. The backend resolves the selected `fast | medium | slow` mode from the Project's snapshotted Channel DNA `pacing_profiles`; it does not own a global numeric table. The external author may not retune those supplied seconds. During authoring the resolved profile is a binding realized-density target, not a beat quota: a section that materially misses its intended cadence must be rewritten when supported story/evidence progression is available, rather than faking density with punctuation fragments, rhetorical punches, arbitrary cutaways, or infographic filler.

Script Analysis owns the semantic `pacing_density` classification for every frozen Script section. The enum remains `high | medium | low`, but the meaning is drawable visual-refresh opportunity rather than narrative/emotional intensity, section importance, or `section_role`. Analysis performs a blind post-freeze rescan of actual story/state changes plus distinct source-owned explanatory/editorial facets; it does not copy the earlier plan or choose/alter section WPM. A quiet or evidence-heavy section may therefore still classify as visually dense when the narration honestly supplies many independently drawable contributions.

Scene Breakdown receives the exact frozen video pacing profile plus each section's locked WPM. First-pass boundaries remain semantic and strong rhetorical/same-dominant-still merge-back remains authoritative. Only after those passes does `static_image_v5` estimate beat durations and require a conformance rescan: long results search for missed genuine states, short results search for false fragmentation. Numeric pacing alone never manufactures, deletes, or rejects a semantic beat.



## Continuity review

The imported continuity catalog is an authoring proposal that can be reviewed before Style Bible generation. The user may remove or restore whole `characters`, `locations`, `recurring_props`, and `recurring_visual_subjects`. Removal does not edit Script narration, quotes, presence ranges, or the immutable imported Analysis artifact. It only removes that canonical identity/style lock from the effective downstream continuity set. Visual motifs are not identity/style locks and are not part of this remove/restore control.

If a continuity review change occurs after Style Bible or later derived artifacts already exist, those downstream pointers are invalidated and must be regenerated from the new effective continuity set. Retained canonical IDs are never renumbered.
