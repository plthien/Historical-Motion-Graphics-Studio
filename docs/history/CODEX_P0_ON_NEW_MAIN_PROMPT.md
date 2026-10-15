# Codex prompt — P0 on the NEW Historical Motion Studio main codebase

**Repository:** plthien/Historical-Motion-Graphics-Studio
**Base branch:** main, NOT the repository's possibly misleading default branch. At plan creation time main SHA was ea403973e6c6b23e5c3f073e83ca07ef9a8ca768; re-fetch main before implementing and record current SHA.
**New work branch:** feat/p0-history-remotion-on-main (or another dedicated feature branch from current main). Never write or merge to main without operator approval.

## Read first

1. docs/history/MAIN_CODEBASE_AUDIT_2026-10-15.md
2. docs/history/HISTORY_STUDIO_MAIN_BASED_MIGRATION_PLAN_V2.md
3. README.md, 00_START_HERE.md, docs/99_SOURCE_OF_TRUTH.md, docs/68_EXTERNAL_AUTHORING_RULE_COMPLIANCE.md (YTB descriptive baseline only; its one-image-per-beat rule must NOT govern history product).
4. src/App.tsx, src/pages/StudioPage.tsx, src/features/studio/PromptImportWorkspace.tsx.
5. app/main.py, app/db/models.py, app/core/external_authoring.py, app/services/artifacts.py, app/modules/v4/voice_service.py, app/modules/v4/render_service.py, app/modules/v4/durable_render_service.py, app/modules/v4/render_graph.py and their relevant tests.
6. Existing separate R0 prototype at ref feat/r0-remotion-history-motion-poc, paths experiments/remotion-history-poc/, docs/architecture/EXTERNAL_CHATGPT_AUTHORING_CONTRACT.md. This branch DOES NOT contain the new main codebase. Copy only files after inspection; do NOT merge it wholesale.

## Product invariants

- NO LLM APIs. All creative and analytical work via manual External ChatGPT prompt/download/import/validate/review/approve. P0 introduces no model runtime or creative agent.
- Preserve existing Cockpit UI and the exact external JSON parsing/approval and Voice infrastructure at P0; do not modify production app/main.py, routes, DB, migrations, source-of-truth README or old YTB pipeline until planned clean-cut.
- The new engine is Remotion archival multi-layer motion graphics, NOT Spine and not still-image-per-Beat.
- Do not claim 60 seconds render passes merely because old backend/frontend/FFmpeg CI passes. Real Remotion Player/browser render/FFprobe and screenshots required.
- No copyrighted archival assets without reviewed license; diagrams/historical maps without period evidence must say SCHEMATIC.
- Never push to main; no self-merge or closing another PR.

## Implement P0 only

1. Create a machine-verifiable file inventory for current main assigning KEEP/EXTRACT/ADAPT/REPLACE/DELETE and exact new owner, at least for every YTB production module and UI panel. Identify legacy tests preserving behavior that must be extracted before deletion.
2. Establish baseline exact SHA test results for Python 3.12, frontend Node test/build, existing Windows process guards, migration head and current QA; record commands actually run or label BLOCKED.
3. Port reviewed standalone Remotion prototype code from OLD feature branch to a new experimental subdirectory on this main-derived branch; preserve root Node/Python locks and old production pipeline. Fix dependency version matching and commit a POC-local package-lock.json.
4. Harden all plan validators (invalid null, arrays, unknown fields, event bounds, duplicate IDs, local-only asset paths/FS containment) and add negative tests.
5. Verify @remotion/player preview uses same composition as Remotion CLI renderer; verify setup/event useCurrentFrame and Sequence timing at boundaries. Confirm actual browser seek/play rather than assuming correctness.
6. Replace sample visual placeholder using operator-approved/public-domain archival materials only if rights and actual source metadata can be recorded. Otherwise label visual source acceptance BLOCKED. Demonstrate documentary collage with paper, photos, kinetic type, stamps, diagrams/maps and 30fps layer-by-layer reveals; avoid 20-second static slides and empty frames.
7. Create dedicated POC CI (npm ci + npm tests + Vite build + headless real render + FFprobe screenshots/artifacts); require an independent Windows render/preview and sampled frame review. Ensure POC H.264 exactly 1920x1080, 30fps, duration 60s. POC may be SILENT; don't present it as a completed narration film.
8. Report technical/visual review separately, updated source SHA and exact test command outputs. Keep the PR Draft until everything actually passes; otherwise mark PARTIAL/BLOCKED with reasons.

## Acceptance and scope guard

**P0 PASS requires:** reproducible POC npm ci/test/build, Player screenshot/seek, real H.264 render, FFprobe metadata and frame gallery, source asset provenance, exact-SHA CI and reviewer visual comparison. No harm to old YTB code/tests or main. No LLM calls.

**NOT P0:** implement history production DB/API, modify main Studio stages, voice extraction, asset discovery, full Visual Director, history renderer host, timeline editor or old route deletion. Those belong to P1–P8 in the migration plan.

**Final Codex report:** starting main SHA, phase branch & PR, changed files, tests pass/fail/not run, rendered file/screenshots/FFprobe and licensing provenance, self-review findings, whether dedicated P0 gates are actually verified, explicit PASS/PARTIAL/BLOCKED, next dependencies. Do not submit a speculative claim of completed historical end-to-end video.
