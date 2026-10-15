# P5 — External ChatGPT Visual Director and deterministic multilayer compiler

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no local LLM, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Prerequisites
P2 approved exact script/claims + P3 approved FINAL audio/true word alignment + P4 approved asset versions. P5 cannot use fake timestamps or candidate-only research URLs.

## Models and approved creative grammar
- `HistoryMotionStyle.v1`: approved palette/print texture, typography/font source, safe regions, timing defaults, motion preset registry and historical data integrity policy.
- `VisualMotionPlan.v1` from External ChatGPT JSON: source ID/hash bundle, stable `setup_id`, semantic `start_word_id/end_word_id`, explicit enter/hold/exit/continuity, ordered `layers` with stable IDs, types `archival_photo|portrait|map|headline|time_label|route|line|stamp|diagram|annotation|paper_texture|caption|camera`, approved `asset_version_id` where necessary, normalized bounding boxes/crop/position/z and `motion_events` (event_id, layer_id, cue word/phrase ID, offset_ms, duration_ms, preset, easing, exit_mode).
- `CompiledTimeline.v1` server-owned: fps/resolution, final audio SHA, approved assets version SHA array, setups and events in exact integer frames, determined rendering preset parameters and explicitly bounded transitions; canonical hash and composition contract version.
- Preset registry examples: fade-in/out, paper-slide, stamp-pop, annotation-draw, route-draw, type-reveal, image-mask-wipe, parallax-pan, Ken-Burns local-photo crop, camera-push. NO inline JSX/JS/CSS/custom equations from external JSON.

## Backend/API ownership
- New `app/history/director/{prompt_builder,validator,repair,service}.py`, `app/history/timeline/{compiler,frame_math,layout,typography,continuity,fingerprint}.py`, `contracts/history/schemas/visual_motion_plan.v1.json` and compiled timeline schema, tests/history/director.
- POST/GET `.../visual-director/prompt`, POST `.../visual-director/import/validate`, POST `.../visual-director/import`, POST `.../visual-director/approve`, POST `.../visual-director/repair-prompt`.
- POST `.../timeline/compile` produces candidate when inputs valid; GET `.../timeline/current`, GET `.../timeline/validation-report` and POST `.../timeline/approve` guarded CAS. Candidate compile NEVER silently approves or mutates parent artifacts.

## UI/External ChatGPT
- Visual Director workspace: prompt summary lists frozen script, actual word timings, approved asset inventory, style profile, exact schemas and allowed preset IDs. User downloads prompt and obtains JSON file from External ChatGPT.
- Show source-hash match/mismatch, review each persistent setup and layer list, cue/asset associations, timeline density/legibility warnings. Build targeted repair prompt limited to invalid setups; preserve locked valid event IDs and approved child references.
- No magical auto-adjustments of script/voice/historical claims; only deterministic layout corrections that are explicit compiler policies, with transparent QC output.

## Compiler decisions and exact order
1. Freeze schema and word-anchor resolution, canonical serialization (Unicode NFC policy and key order), time-to-frame quantization with explicit rational fps and integer sample alignment.
2. Implement semantic validator and source identity/approved asset verification; reject overlapping setup errors except explicit crossfades, missing events, phantom IDs, unknown layer kinds/presets, unapproved/unverifiable license assets.
3. Resolve global setup intervals and per-layer event starts; build controlled normalized transform curves within safe bounds. Allow one persistent setup across multiple paragraphs/section boundaries; do not force one image per beat.
4. Layout/typography validator: title bbox, font/glyph loading, overlap/safe area; visible text must be tied to script/researched source when factual.
5. Approve candidate and compile deterministic immutable timeline with fingerprints (script, WAV, alignment, asset versions, style, planner schema/version). Same input → identical bytes/hash.
6. Changes in text/WAV/asset/preset invalidate precisely affected descendants. A single label cue revision must not regenerate voice or unrelated asset media.
7. Add UI preview data DTO, tests and Stage readiness wiring; render engine comes in P6, not simulated during P5.

## Gate / adversarial tests
- Duplicate IDs; setup gaps; crossfade not specified; invalid 30fps rounding; cue outside final WAV; same word repeated but ID mismatch; asset content-hash drift; unsupported remote media; unapproved copyright status; CSS/JS injection; 0/negative/extreme coordinates; invisible title; mixed-language font fallback and single-frame duration.
- Determinism golden fixtures run twice across OS; exact normalized hash equality, no floating drift; generated revision preserves immutable earlier versions. No fallback to one still per beat or internal LLM call.
- Exact test/run/PR proof; no root MANIFEST.json/check.
