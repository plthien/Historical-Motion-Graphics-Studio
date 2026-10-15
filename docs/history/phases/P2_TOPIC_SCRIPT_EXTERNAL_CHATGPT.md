# P2 — External ChatGPT historical topic and script authoring

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no LLM local, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Precondition
P1 accepted: history-only API/frontend/DB with zero old YTB product support; don't import app/modules/v4. Human operates External ChatGPT manually.

## Artifacts and typed contracts
- `HistoryChannelDNA.v1`: language, audience, historical niche, narration identity (voice/delivery/tone bounds), editorial look, map treatment, uncertainty/personal-study policy.
- `TopicIdeas.v1`: reserved artifact/project IDs, parent fingerprint, ideas[] (idea_id/title/hook/period/region/story_angle/novelty_research_questions/factual_risks). `SelectedTopic.v1` includes chosen idea or manually created topic and source reference.
- `ScriptHistoricalAnalysis.v1`: EXACT full_script and section-level exact_text + stable IDs, claim IDs with historical timeframe/location/source leads/verification_state (unverified/verified/disputed/hypothetical), narration cues/overrides, source-fingerprint. Script and claims analysis are one combined external ChatGPT output and one atomic publication head (or paired CAS if separate storage).
- Stage schemas under `contracts/history/schemas`, prompts under `contracts/history/prompts`; no old Topic/Script schema fallback.
- Build Prompt reserves identifiers, freezes ChannelDNA/topic/avoid list and JSON schema; user downloads prompt, manually runs ChatGPT, imports **downloaded JSON file**, independent Validate then Import and explicit Review/Approve.

## Proposed server API (final DTOs via OpenAPI parity)
- POST/GET `/api/history/projects/{id}/topic/prompt`; POST `topic/import/validate`, POST `topic/import`, POST `topic/approve`, GET `topic/current`.
- POST `topic/manual` with one user-selected direct topic; same artifact lineage/approval as imported idea.
- POST/GET `.../script-analysis/prompt`; POST `script-analysis/import/validate`; POST `script-analysis/import`; GET `script-analysis/review`; POST `script-analysis/approve`; GET `script-analysis/current`.
- POST `.../{stage}/repair-prompt`: deterministic prompt explaining exact failed paths with frozen successful context; NEVER invokes model.
- Validate-only reads state, no artifact/head/reservation consumption. Import revalidates and stores candidate, Approve CAS-checks source and publishes new immutable content hash.

## UI components and behaviors
- `src/features/history/topic/TopicWorkspace.tsx`, `script/ScriptHistoricalAnalysisWorkspace.tsx`, shared PromptImportWorkspace extraction.
- Build/Copy/Download Prompt, file JSON upload, Validate, Import, Review and Approve; distinguish unverified candidate historical source citations from verified source records. Prompt reservation survives reload; rebuilt prompt invalidates old reservation explicitly.
- Selected Topic can be manually changed only by producing a new artifact; changing it invalidates approved downstream script but not unrelated Channel settings.
- Exact spoken text is immutable after script approval; edits create a new version. App must not invoke LLM or quietly rewrite narrative.

## Implementation and negative tests
1. Implement schema fixtures (valid, duplicate ids, null nested fields, wrong/unknown props, oversized JSON, invalid enum, wrong project IDs).
2. Port clean external JSON parser, reservation and CAS patterns from P1's neutral implementation; add prompt fingerprint bundle and no hidden model execution.
3. Implement topic builder/import/approve plus manual topic; run API and UI tests.
4. Implement script+claims bundle, exact text integrity and chronology/claim-reference validation; support uncertain/disputed tags and source-candidate review.
5. Add stale source/handoff/replay and racing imports/approvals tests; reload restores prompt; invalid import never changes prior approved head.
6. UI/API parity tests: every enabled button has matching route and typed response; no old YTB prompt schema/route.
7. End-to-end acceptance: idea→external ChatGPT Topic JSON→approve→external ChatGPT Script JSON→approve; restart maintains exact approved script and claim set.

## Gate
No LLM/provider SDK/config/endpoints; zero old data/schema; all strict validation, CAS and E2E/Windows UI tests PASS on exact SHA. No invented fact-check claims; source leads remain unverified until P4.
