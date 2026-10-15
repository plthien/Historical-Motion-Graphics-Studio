Create the complete Step-1 Scene & Beat Plan from the supplied backend-owned Scene decision surface.

Use every supplied section and scene_key exactly once and in exact order. For each Scene, return one `partition` object with an ordered subset of that Scene's supplied legal `boundary_ids`. `boundary_ids: []` means one Beat for the whole fixed micro-Scene and is valid only when the derived Scene-level progression genuinely needs one keyframe; it is not a default starting point. Author exactly one concise semantic metadata object per resulting Beat.

Backend supplies fixed micro-Scenes and technically legal Beat boundaries only. It does not recommend Beat partitions. Step 1 owns the semantic boundary-subset choice: select only boundaries needed to realize useful source-owned keyframes or independently drawable explanatory/editorial facets in the complete Scene progression.

Each compact `sections[]` entry supplies `preferred_hold_seconds.min/max`, the backend-resolved section-level visual-refresh cadence guidance. It is not a Beat-count quota, minimum Beat duration, command to cut every N seconds, independent boundary authority, or permission to reconstruct hidden timing.

Treat each Beat as one keyframe in the Scene's ordered visual sequence, not as a narration chunk. There is no default Beat count: do not start from one Beat / `boundary_ids: []`, minimum cuts, maximum cuts, sentence count, or duration. First derive the complete Scene-level keyframe progression as if planning a short animation without timing arithmetic. Second map those keyframe transitions to supplied legal boundary IDs. Only after keyframe derivation and legal-grid mapping, use preferred_hold_seconds as a secondary cadence sanity/rescan signal. A visually sparse sequence should trigger a rescan for missed genuine source-owned keyframes; an overly fragmented sequence should trigger a rescan for redundant near-duplicates. A boundary may be added or removed only when normal semantic/keyframe tests independently justify the change. Preferred hold alone is never sufficient justification.

Do not calculate Scene duration / preferred hold or word count / WPM / hold to produce a target Beat count. Do not divide narration into equal time chunks, reconstruct timestamps, Beat durations, or hidden timing atoms, or choose the legal boundary nearest a desired second count. Do not reject a genuine short keyframe because it may be below `min`, and do not create a fake keyframe because a hold may exceed `max`. Exact realized Beat timing remains backend-owned after materialization.

**SEMANTICS DERIVES THE KEYFRAME. PREFERRED HOLD MAY TRIGGER A RESCAN. TIMING ALONE NEVER CREATES THE KEYFRAME.**

A small source-owned visual delta may justify its own Beat when it helps faithfully express the Scene progression; a large change is not required. Scene duration and preferred hold guidance do not determine Beat count or minimum Beat duration, so even a micro-Scene at the preferred minimum may require multiple keyframes. A Beat may legitimately fall outside the preferred band when source truth, causal order, future-source safety, one-frozen-image integrity, or the available legal boundary grid requires it.

Preserve the future-source firewall and global continuity. The first Beat of the whole plan must be new_setup; later Scene and Section boundaries do not imply reset. A mandatory first Beat of a fixed later Scene may be an honest continuity hold only when its opening source authorizes no new keyframe state; a subtle source-owned delta is progression, not a hold merely because it is small. Do not use continuity holds to reduce Beat count. Internal Beats require an independent source-owned contribution.

Return the reserved artifact_id and schema-valid JSON only.

# Input JSON

{{input_json}}

# Final acceptance reminder

Before returning JSON verify:

- every supplied Scene appears exactly once;
- every selected boundary ID was supplied for that Scene and remains in source order;
- `boundary_ids: []` is used only when the derived Scene progression genuinely needs one keyframe, never as a starting heuristic;
- Beat metadata count is exactly selected boundary count plus one;
- optional Beats have independent source-owned meaning;
- a mandatory Scene-start continuity hold does not invent novelty;
- hiding all source after each Beat (including later source) does not invalidate that Beat;
- timing did not manufacture semantic material or a boundary with no independent source-owned contribution;
- Beat count was derived from the Scene's visual keyframe progression, not from one-Beat/minimum-cuts bias, Scene duration, or preferred hold guidance;
- preferred_hold_seconds was consulted only after keyframe derivation and legal-grid mapping as a secondary rescan signal;
- every boundary added or removed after that rescan has independent source-owned semantic/keyframe justification;
- no target Beat count, equal-time partition, reconstructed timing, or nearest-seconds boundary heuristic was used;
- exact timing remained backend-owned;
- no useful keyframe was rejected merely because its visible delta is small or its realized timing may fall outside the preferred band;
- Scene and Section boundaries do not automatically reset continuity.
