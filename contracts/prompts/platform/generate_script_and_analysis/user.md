Create the complete Script and Script Analysis for the supplied project Input JSON.

Apply the active channel authoring skill only for the phase currently being executed: Script authoring and narration performance before narration-text freeze, Voice Annotation after narration-text freeze, and Script Analysis after Script-payload freeze.

Do not apply downstream Scene directing as a Script rewriting authority. After the Script is frozen, the Analysis phase must not rewrite it.

# Task sequence

1. Resolve the authoritative project inputs.
2. Plan the complete Script architecture and choose one exact `planned_narration_wpm` per section inside the supplied `user_input.narration_wpm` range before drafting prose.
3. Draft the complete Script to the requested duration and selected pacing profile.
4. Complete required research, factual-confidence, story-architecture, runtime, visuality, retention, skeptical-viewer, and pre-freeze realized visual-density checks.
5. Complete the active channel skill's Narration Performance composition/rehearsal pass while prose is editable.
6. After every other prose-changing pre-freeze correction is finished, run the **Final exact-text Human Performance / Virtual Table Read pass** on the latest narration. This is the last prose-quality gate before freeze. If it rewrites prose, rerun dependent pre-freeze gates and then rerun this final performance pass.
7. Freeze every section's narration text only after the exact final narration passes that last performance review.
8. Against the frozen narration only, create `spoken_overrides` and `delivery_cues`.
9. Freeze the complete Script payload.
10. Build Script Analysis from that exact frozen Script, including a fresh production-readiness pass on every canonical character/group so the existing `description`, `explicit_visual_traits`, `identity_markers`, and `continuity_notes` fields are sufficiently specific for downstream visual continuity without inventing unsupported history.
11. Partition the complete frozen narration into a non-overlapping `visual_context_catalog`; every non-whitespace source character must resolve to exactly one context.
12. Add only stable intrinsic facts to each non-motif continuity entity's `physical_constraints`; use an empty array when no such fact is justified.
13. Verify all exact quotes/ranges and return schema-valid JSON.

# How to use the input

Use only the current task inputs and fresh external research performed during this run when factual or visual verification is needed. Do not use remembered research, competitor findings, factual conclusions, or project data from prior chats/sessions as authority. Earlier-stage data may be used only when it is explicitly present in this Input JSON.

- `channel_context.name`: The channel display name only. Use it only to identify which channel this task belongs to; do not infer style, audience traits, performance history, creative rules, reputation, or channel history from the proper noun.
- `channel_context.language`: Write authored viewer-facing Script and Analysis prose in this language. Proper nouns may retain their accepted names.
- `channel_context.niche`: Supplies subject-scope context for relevance and adjacent themes. It does not create factual authority by itself.
- `channel_context.audience`: Supplies intended viewer context for accessibility, relevance, assumed background knowledge, curiosity, and payoff. Do not invent unstated demographics, expertise, preferences, or behavior.
- `narration_identity`: Defines how narration should sound and behave. Privately resolve its `voice`, `delivery`, `explanation_style`, `tone_boundaries`, and `avoid` together. It controls narration behavior, is not a closed style enum, and does not define factual truth. Do not impose a generic entertainment, documentary, comedic, solemn, calm, tense, poetic, or cinematic personality from the niche, visual style, or pacing mode. Do not let evidence caution or technical material flatten a supplied personality into neutral documentary prose.
- `topic_idea`: The selected Topic Idea the Script must realize. `title`, `core_question`, `viewer_hook`, and `topic_angle` establish the selected video's audience-facing promise, question, and scope; the Script may naturally develop them but must not silently become a different video. `content_tags` provide subject/editorial context. `risk_notes` identify factual or framing cautions to resolve through current research or careful scripting; they do not authorize unsupported claims. `tone`, `pov_style`, `avoid`, `recommended_pacing_mode`, and `pacing_mode_reason` are Topic-level recommendations only.
- `topic_idea.tone`: The selected video's recommended tonal modulation inside the supplied `narration_identity`. Use it to shape this Script's local mood or attitude when compatible with current task constraints. It does not replace the narration identity, override its `tone_boundaries` or `avoid` guidance, or create factual authority.
- Current choices override Topic-level recommendations: `user_input.pov_style` overrides `topic_idea.pov_style`; `user_input.avoid` overrides `topic_idea.avoid`; `user_input.pacing_mode` plus `video_pacing_profile` overrides `topic_idea.recommended_pacing_mode`. The Topic Idea's pacing recommendation remains context only after the user selects or overrides a mode.
- `user_input.target_duration_minutes`: The whole-video runtime target and planning authority.
- `user_input.pov_style`: The current editable Script point-of-view, story-engine, and evidence-delivery choice.
- `user_input.avoid`: Current Script-specific exclusions or guidance. Treat it as stronger than the Topic Idea's avoid recommendation.
- `user_input.section_count_mode`: The user's selected section-planning mode.
- `user_input.pacing_mode`: The currently selected overall video pacing mode.
- `video_pacing_profile.mode` and `video_pacing_profile.profile_name`: Identify the selected pacing calibration.
- `video_pacing_profile.policy`: States the hard meaning and limitation of that selected calibration.
- `video_pacing_profile.density_profiles.high|medium|low`: Section-level visual-density bands for realized semantic/drawable visual cadence, not quality scores, emotional intensity, or narration speed. Each `preferred_hold_seconds.min/max` is the supplied preferred still-image hold range for that density. Do not convert these values into Beat quotas, sentence-length rules, arbitrary cuts, or WPM targets.
- `authoring_constraints`: The hard operational section-planning contract. `section_count_mode` controls which section-count rule applies. For `llm_decides`, choose a coherent architecture within `minimum_section_count` and `maximum_section_count`; treat `recommended_section_count` only as guidance. For `backend_plan`, follow the supplied `section_plan` exactly: `section_index` defines order and `section_role` defines the required role. `user_input.narration_wpm` is the only model-facing WPM authority.

Use `user_input.narration_wpm.min` and `.max` as the inclusive section-WPM envelope. Every Script section must output one exact integer `planned_narration_wpm` within that range. Choose section WPM deliberately from spoken-delivery needs; do not mechanically derive it from `fast | medium | slow` video pacing or `high | medium | low` visual density. Runtime is the sum of section-local durations at those chosen values.

Apply `authoring_constraints.section_count_mode` exactly:

- for `backend_plan`, return exactly one Script section for every item in `authoring_constraints.section_plan`, in the same order, with the exact corresponding `section_role`;
- for `llm_decides`, choose a coherent architecture within the supplied minimum/maximum count; treat the recommendation only as guidance.

The first returned section must function as the hook / cold open, establish the video's central promise, and have **`high` pacing density**. The narration itself must contain enough genuine drawable progression to earn `high`; do not relabel a sparse hook.

# Required pre-freeze outcomes

Before narration-text freeze:

- the story architecture must be complete and causal;
- research must be complete when material;
- factual confidence must match evidence;
- major abstract/technical/evidence points must be checked for a useful ordinary-human consequence or translation when the supplied narration identity calls for practical experiential explanation;
- narrator personality must remain recognizably consistent through evidence-heavy and uncertainty-heavy material;
- each section must have genuine drawable progression appropriate to its intended density/profile, using story/state changes or distinct source-owned explanatory/editorial facets rather than camera-only or punctuation-only cuts;
- the pre-freeze realized visual-density gate must pass;
- the Narration Delivery Fit / Narration Performance gate must pass;
- the prose must sound natural when spoken rather than mechanically segmented, breathless, formulaic, or essay-like.

Use the active skill for **how** to achieve these outcomes.

# Voice-first authored/spoken separation

Canonical narration is reader-facing authored text, not provider text.

Preserve natural written notation. Do not rewrite identifiers or numbers into phonetic words merely to make TTS easier or to keep `spoken_overrides` empty.

For example, prefer authored:

```text
Model X204 costs $19.50.
```

Do not pre-normalize it merely for TTS as:

```text
Model X two zero four costs nineteen dollars and fifty cents.
```

After narration-text freeze, represent intended pronunciation separately with `spoken_overrides`.

# Spoken override audit

Every Script section must contain `spoken_overrides`.

Run the mandatory sensitive-token scan on the frozen narration:

- every ASCII digit and every `$`, `€`, `£`, `¥`, or `%` must be covered by exactly one override;
- do not pre-normalize those tokens out of canonical narration to avoid the scan;
- quote/context anchors must resolve exactly and unambiguously;
- `spoken_text` must be plain spoken text with no SSML or provider markup;
- override spans must not overlap.

# Delivery cue audit

Every Script section must contain `delivery_cues`.

Explicitly audit every section for intentional perceptual pause intent. Audit both performance/thought-group pauses and rhetorical/dramatic pauses. Empty is valid only when that audit finds no pause worth preserving reliably in synthesis.

Use only selective `pause_after` cues with `pause_strength` `short`, `medium`, or `long`. Do not provide milliseconds, seconds, speed, emotion, style, SSML, or provider fields.

Do not mechanically map every narrative marker, sentence, paragraph, reveal, or punctuation mark to a cue. Do not minimize cue count merely because cues are optional, and do not maximize cue count for artificial expressiveness. High-importance reveal, turning-point, contrast, climax, and payoff markers are cue candidates, not automatic pauses.

# Freeze and Analysis

After voice annotation, freeze the Script. Do not revise narration during Analysis.

Use `sec_001`, `sec_002`, and so on according to Script order.

Every analysis quote, context quote, spoken override quote, delivery cue quote, and every `presence_ranges` start/end boundary must occur verbatim in the referenced frozen narration and resolve unambiguously.

For every section, independently classify `analysis.section_guidance[*].pacing_density` from the frozen narration's **actual drawable visual-contribution frequency**, including genuine state changes and distinct source-owned explanatory/editorial facets. This is visual refresh opportunity, not narrative or emotional intensity. `sec_001` must be `high` by contract and must have been authored to deserve it. For later sections, do not infer density from `section_role` alone or blindly preserve earlier plans. Never alter or invent section WPM during Analysis. Do not copy any WPM field into Analysis unless the supplied Analysis schema explicitly contains one.

The Analysis payload must include every required schema collection: `section_guidance`, `narrative_markers`, all required `continuity_catalog` arrays (`characters`, `locations`, `recurring_props`, `recurring_visual_subjects`, `visual_motifs`), and `visual_context_catalog`. Required arrays remain present when legitimately empty; do not omit them and do not add schema-unknown fields.

# Character production-readiness contract

Do **not** add new Character schema fields. Use the existing `description`, `explicit_visual_traits`, `identity_markers`, and `continuity_notes` fields with non-overlapping jobs so downstream continuity stays useful without turning Analysis into a second style guide.

Character Analysis must not become a global rendering or image-style specification. Character description is the authority for **who the character is and what era/context-specific visual cues identify them**; later visual-authoring stages receive their own visual-style guidance. Do not encode rendering-language constraints such as meme-stick construction, hand-drawn linework, flat rendering, palette rules, photorealism negatives, camera framing, or reference-image composition inside Character Analysis fields.

For every canonical `human_character`, resolve only the minimum useful identity content when relevant to the frozen Script, authoritative project inputs, research, or an explicitly labeled representative reconstruction choice:

- approximate visual age or age band;
- human species/type, historical era, modern/other context when that distinction materially affects appearance;
- sex presentation only when authored or intentionally selected as a production continuity choice;
- relative body scale compared with other recurring characters;
- a simple hair/grooming silhouette;
- a simple garment silhouette and material/context family;
- one restrained recurring color assignment or color relationship when useful for identity.

Do not force every category when it is irrelevant, unsupported, visually useless, or would create false certainty. The goal is **minimum sufficient production specificity**, not costume design or style duplication.

Use the existing Character fields with these exact jobs:

- `description`: a compact **WHO/WHEN** identity card. In two to four short sentences, state the recurring person/group identity, approximate age/context/species when relevant, relative scale, and only the stable intrinsic appearance or era-specific wearable cues needed to recognize the entity. Aim for roughly 450 characters or fewer when practical and always stay inside the schema limit. Do **not** include visual style/rendering terms, camera/composition instructions, negative-prompt language, section IDs or section names, temporary actions, scene summaries, the character arc, or narrative role; `narrative_role` owns story function.
- `explicit_visual_traits`: usually three to seven short, atomic, directly visible, stable facts that can be drawn without scene context. Good traits include age/scale, hair or facial-hair shape, stable garment silhouette/material/color, stable body-scale cues, and a truly persistent accessory when authored. Do **not** include actions, temporary handheld items, story function, section references, style/rendering instructions, negative constraints, or reconstruction/evidence boilerplate.
- `identity_markers`: internal continuity metadata only. Choose roughly two to five of the most discriminative stable cues that are already consistent with the visible traits; do not turn this into a second description. Do not include narrative role, scene/section references, style instructions, actions, or generic era prose. These markers are not intended to be copied as a separate block into the Visual Reference generation prompt.
- `continuity_notes`: concise stability/change/provenance rules only: what must remain stable, what may change, explicit age/development progression, important relative-scale relationships, and which choices are production reconstruction rather than evidence. Do not repeat a channel-wide visual baseline in every character, do not add rendering/style instructions, and do not use section IDs merely to express scope already represented by `presence_ranges`.

For fictional representative characters, stable production-only visual choices may be selected when needed to prevent image-generation drift, provided they:

- do not contradict the frozen Script, supplied current-task inputs, or freshly verified research;
- remain visually simple and intrinsic to the character rather than scene-specific;
- are explicitly described as production/reconstruction choices rather than recovered historical facts;
- avoid unsupported culture-specific, biological, ethnic, status, kinship, or named-individual claims.

For named, documented, or real-world people, do not invent unsupported appearance merely to fill the character card. Stable appearance must come from authoritative evidence or remain unspecified.

If a recurring character visibly ages, changes era/context, changes condition, or intentionally changes garment/state, that transition must be supported by the Script or authoritative inputs. Analysis must not invent an unearned state transition after Script freeze.

If the movie benefits from reusing the same adult, child, peer, caregiver, worker, or other recurring person, establish same-identity continuity explicitly in the editable Script before freeze when generic labels would otherwise be ambiguous. After Script freeze, Analysis may not convert several generic adults or children into one canonical person merely for visual convenience.

For `human_group`, describe it as a recurring group/social-unit continuity entity rather than pretending it is one person. `description` should contain only stable group identity/context; `explicit_visual_traits` should contain only visible shared or compositional traits that persist; `identity_markers` should contain only the smallest discriminative group-continuity cues; and `continuity_notes` should contain only stability/change/provenance rules. Do not invent exact group size, kinship, hierarchy, ethnicity, costume uniform, or membership not supported by the Script.

Do not force prehistoric styling onto a modern comparison character or modern styling onto a prehistoric reconstruction unless the Script explicitly calls for that contrast.

# Input JSON

```json
{{input_json}}
```
