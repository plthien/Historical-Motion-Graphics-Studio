# Script & Analysis Runtime + Continuity Review — v16 Implementation Record

Status: approved implementation scope.

## Rule-compliance snapshot

- Baseline: `v3.0.0-external-authoring.15-rc1`.
- Compatibility policy: no compatibility readers, schema-version dispatch, backfill, conversion, or old-flow fallback. Development data/artifacts remain disposable.
- Database policy: no structural database migration is required for this scope. Continuity review state remains an immutable JSON artifact keyed by project logical ID; the effective review must match the current Script Analysis lineage.
- Ownership: `app.modules.platform` owns Topic pacing recommendation handoff, combined Script & Analysis, deterministic video pacing resolution, and continuity review; `app.modules.v4` consumes frozen Script pacing data for Scene Breakdown/timing and consumes the effective continuity set for deterministic Style Bible generation.
- External authoring: no internal LLM or web-research execution is introduced. Topic, Script + Analysis, and Scene Breakdown imports remain strict, immutable, reservation-bound, and CAS-published.
- Runtime authority: `target_duration_minutes` is requested runtime. Each Script section owns one locked `planned_narration_wpm`; backend runtime is the sum of section-local planned durations and whole-Script `planned_average_wpm` is derived only as an aggregate diagnostic. Actual TTS timing remains final downstream authority.
- Visual pacing authority: Topic Ideas recommends `fast | medium | slow`; the user may override at Script prompt build; backend resolves the exact profile deterministically. The selected profile is a binding realized-density authoring and post-segmentation conformance target, never a beat-count quota.
- Script Analysis semantics: Analysis independently classifies actual frozen section `pacing_density`; it does not copy planned density or select/change section WPM.
- Scene Breakdown semantics: first-pass segmentation and strong rhetorical merge-back remain semantic-only. `static_image_v5` uses local section WPM after semantic stabilization, and material profile mismatch requires rescan for missed genuine states or false fragmentation without numeric manufacture/rejection.
- Continuity semantics: remove/restore affects only downstream identity/style locking for whole characters, locations, recurring props, and recurring visual subjects. Script narration and imported Analysis bytes are unchanged.
- Downstream invalidation: changing the effective continuity review invalidates Style Bible and every derived project pointer.
- UI scope: Topic cards show pacing suggestion/reason; Script exposes pacing mode plus per-section planned pace; Scene review exposes section pacing-conformance telemetry. No new Studio stage.

## Acceptance criteria

1. Topic candidates always recommend a valid pacing mode and reason; selection preloads that recommendation once while preserving user override.
2. FAST/MEDIUM/SLOW resolve deterministically to the approved profile ranges and are frozen into Script lineage.
3. Every Script section declares valid `planned_narration_wpm`; backend derives runtime from section-local WPM and has no old global-WPM authoring fallback.
4. Script authoring prompt requires a pre-freeze realized-density gate against the selected profile without beat quotas or rhetorical fragmentation.
5. Script Analysis independently reports actual frozen `pacing_density` and does not alter section WPM.
6. Scene Breakdown consumes the same frozen profile and local WPM, stays semantic-first, preserves strong merge-back, and uses `static_image_v5` conformance rescan without numeric beat rejection.
7. User can remove/restore whole supported continuity items without changing Script or imported Analysis; downstream identity authority remains unchanged from v15.
8. Style Bible and downstream canonical reference authority contain only retained continuity items; review changes invalidate derived pointers.
