Produce the complete **Step 2 — Visual Plan** for the supplied locked Scene & **Visual-Semantic Beat** Plan candidate.

The visual-semantic plan is frozen. Do not change scene/Beat identity, order, source ownership, timing, locked state/delta, continuity/reset authority, or reference eligibility. Adjacent Beats may be facets of one larger proposition; do not merge them conceptually or preload the neighboring facet.

# Required authoring order

For every locked Beat, follow this order exactly:

1. Read `meaning_now`, `visible_state_now`, `delta_now`, `continuity_with_previous`, locked `visual_context_ref`, its exact catalog definition, and eligible canonical refs.
2. Conceive **one concrete frozen image** that honestly communicates the exact current Beat.
3. Check that all visible elements, relationships, qualifiers, refs, text, framing, and camera instructions can coexist in that same image at the same instant.
4. Decompose that image into `visual_concept`, `subject_action`, `environment_emphasis`, and `composition`.
5. Identify visible canonical identities and select their supported refs.
6. Only after the still is complete, classify `representation_family` by its dominant primary read.
7. Add camera/lighting/color support without changing the semantic image.

All visual fields must describe different aspects of that one same image. Do not generate them as separate semantic solutions. `representation_family` is classification metadata, never a family-specific prose-template selector. Do not use the workflow `choose family -> load family template -> paste locked metadata`, and do not pre-allocate Beats to families.

Success means:
- exactly one visual entry for every locked Beat ID, in exact order;
- every visual entry communicates that Beat's own `visible_state_now` and `delta_now`, even when it is only one facet of a larger proposition;
- adjacent Beats with genuinely different locked deltas do not collapse into the same dominant still with cosmetic camera/crop changes; every genuine locked delta, regardless of magnitude, remains legible;
- every visible fact is justified by the current locked Beat and explicit continuity authority alone;
- visual fields perform distinct jobs rather than paraphrasing one another;
- environment describes NOW, not the whole scene or future progression;
- composition uses actual current entities/components and their real geometry, not a reusable prose shell;
- selected refs are eligible, visibly present, and obey relation-specific immediate continuity;
- every visual entry includes one `representation_family` classification for the same generated still;
- representation family is classified from the finished still's dominant primary read rather than used to generate a default lived-scene template;
- if `representation_family` mismatches an otherwise semantically valid still, correct the classification first and do not re-author the still merely to fit the label;
- across the complete Visual Plan, use approximately 54% Character / Environment / Lived Scene, 13% Object / Evidence Close-up, and 33% Text / Diagram / Editorial only as a post-hoc whole-plan calibration after every honest still has been authored and classified; never pre-allocate Beat counts, target exact integer counts, or weaken a Beat to move a percentage;
- short editorial text/graphic treatment may compress supported meaning but never add a factual claim;
- the dominant relation remains readable at a glance on mobile;
- no unsupported evidence/detail is invented.

The 54 / 13 / 33 audit is last. If the distribution is far from calibration, reconsider only Beats where two or more representations are genuinely equally truthful and equally readable. A result near the ratio is not evidence of quality; local semantic truth always wins.

Return the exact reserved Visual Plan Artifact ID and only the schema-valid JSON object.

# Locked Scene Plan Candidate and Visual Context

```json
{{input_json}}
```

# Final acceptance reminder

Before returning the JSON, verify across the entire result:
- no visual entry uses information from a later Beat;
- no Beat image represents the whole larger proposition when the locked Beat owns only one facet of it;
- adjacent Visual-Semantic Beats remain visually distinguishable through their actual locked state/delta, including small genuine deltas, not cosmetic camera-only variation;
- continuity preserves stable context when that helps the viewer read A → B state change;
- no final field is a wrapper/paraphrase of another field;
- environment changes only when current locked state changes, and never preloads future scene facts;
- composition names/places only entities or components actually visible now;
- reference selection is bidirectional: selected refs are visible, visible eligible canonical identities are selected, and `graphic_sequence` does not automatically carry physical canonical refs;
- any `visible_text` or `graphic_layout` is semantically entailed by the current locked Beat and does not add a new fact;
- nearby Beats do not collapse into one repeated representation mode merely because it is convenient;
- lexical variety is not being used to disguise repeated generic visual templates;
- the final self-review occurred in this order: coverage/lock, frozen-image semantic integrity, field roles, canonical reference bidirectionality, template/factory detection, family classification, then whole-plan ratio calibration;
- a family-label mismatch is repaired as classification-only when the still itself is valid;
- repeated wording, lexical overlap, repeated composition, repeated framing/camera, long family runs, and distribution distance are diagnostic warnings only; do not optimize prose or visuals merely to evade them.
