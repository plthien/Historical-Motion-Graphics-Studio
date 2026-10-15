# Canonical Entity Presence Ranges

Status: active in `3.0.0-external-authoring.13-rc1`.

## Purpose

Script Analysis is the semantic authority for recurring canonical entities. Downstream stages must not infer identity scope again from visual prose or exact noun matching.

For `characters`, `locations`, `recurring_props`, and `recurring_visual_subjects`, Script Analysis distinguishes:

- `occurrences`: direct or representative verbatim Script anchors proving the canonical entity exists;
- `presence_ranges`: authoritative Script spans where that same canonical entity remains narratively active, including pronouns, shorthand, renamed mentions, and implicit continuation.

`visual_motifs` remain occurrence-anchored and do not use presence ranges.

## Raw external-authoring contract

Each presence range contains only:

```json
{
  "section_id": "sec_001",
  "start_quote": "exact unique Script text",
  "end_quote": "exact unique Script text"
}
```

Both quotes must occur exactly once in the declared section. A range starts at the first character of `start_quote` and ends after the final character of `end_quote`. Multiple non-overlapping ranges may exist in one section when the narration leaves a canonical identity for a generic/comparative/evidence detour and later returns.

The author must not default to a whole section and must not extend a canonical range across a different, generic, comparative, developmental, or evidentiary identity merely because similar nouns are used.

## Backend resolution and validation

The backend resolves range boundaries against the frozen canonical Script and materializes `resolved_script_span`.

Import fails atomically when:

- the section is unknown;
- either boundary quote is missing or ambiguous;
- range order is reversed;
- ranges are unordered, duplicated, or overlapping;
- a direct occurrence lies outside every range for its canonical entity.

No fuzzy recovery, NLP inference, compatibility reader, backfill, or migration is used for presence ranges.

## Style Bible ownership

Style Bible owns visual identity, not narrative scope. It deterministically preserves Script Analysis presence-range provenance for canonical entities and rejects stored range drift or tampering.

Style Bible edits cannot expand, shrink, or reinterpret the ranges.

## Scene Breakdown ownership

Scene Breakdown remains the per-beat visual-reference selection authority. Its reference catalog receives direct evidence excerpts and relevant presence ranges from the current approved Style Bible/Script Analysis lineage.

A selected canonical reference is supported when at least one condition holds:

1. the beat overlaps direct Script evidence;
2. the beat overlaps the canonical entity's authoritative presence range;
3. the entity is valid immediate same-scene continuity from the previous beat.

Exact label/alias matches outside an authoritative presence range cannot self-authorize a character, location, prop, or recurring visual subject. Visual fields such as `visual_concept` and `subject_action` also cannot self-authorize a reference.

When multiple eligible canonical entities are visibly rendered, Scene Breakdown should select all relevant references.

## Downstream boundary

Prompt Bundle and Scene Assets consume the selected Scene Breakdown refs. They do not resolve canonical semantic scope again.
