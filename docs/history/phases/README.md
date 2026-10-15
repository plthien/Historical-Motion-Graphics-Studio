# Implementation index — Historical Motion Graphics Studio

**Current and only implementation authority:** [STRICT CLEAN-CUT Plan v3](../HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md). This index provides nine **implementation-ready phase documents**, not evidence any phase has been implemented.

**Repository:** plthien/Historical-Motion-Graphics-Studio. Base every implementation branch on verified explicit main or user-approved staging SHA, never on the old R0 branch. Do not push to main. User reviews PRs and approves merges.

**Permanent policy:** no old YTB support/data/DB/migrations/routes/UI; P1 hard-cuts all legacy product runtime BEFORE P2. Do not import/migrate/read old DBs; do not destroy user files on disk outside new tool root. NO LLM API: user manually runs External ChatGPT, downloads JSON and imports it. Remotion handles layered preview/render. **No root MANIFEST.json and NO root manifest generation/verification workflow** (removed on the plan PR).

## Complete per-phase implementation plans

| Order | Phase / detailed specification | Required phase output | Prerequisite |
|---|---|---|---|
| 0 | [P0 — Remotion feasibility](P0_REMOTION_FEASIBILITY.md) | Actual 60s Remotion preview and independently FFprobe-verified H.264 proof, source ownership ledger | Exact current main |
| 1 | [P1 — Atomic legacy clean cut](P1_ATOMIC_CLEAN_CUT.md) | One NEW history-only Studio skeleton with empty history SQLite + no YTB imports/routes/stages | P0 proof |
| 2 | [P2 — External ChatGPT historical topic and script](P2_TOPIC_SCRIPT_EXTERNAL_CHATGPT.md) | Approved exact History Script+Claims JSON, complete manual authoring UX | P1 clean runtime |
| 3 | [P3 — Voice/Alignment](P3_VOICE_ALIGNMENT.md) | Approved final WAV + true aligned word/phrase map | P2 exact script |
| 4 | [P4 — Historical assets](P4_HISTORICAL_ASSETS.md) | Source/date/license-reviewed, immutable project asset versions | P2 exact script |
| 5 | [P5 — Visual Director/compiler](P5_VISUAL_DIRECTOR_COMPILER.md) | User-imported validated multilayer plan → deterministic immutable timeline | P3 voice + P4 assets |
| 6 | [P6 — Production Remotion video](P6_PRODUCTION_REMOTION_VIDEO.md) | First real narrated, source-backed 60–90s H.264 video/preview | P5 compiled timeline |
| 7 | [P7 — Editor and durable Node rendering](P7_EDITOR_DURABLE_RENDER.md) | Layer editor, selective revisions and crash/retry safe Windows renderer | P6 validated pilot |
| 8 | [P8 — QC/Windows release](P8_QC_RELEASE.md) | Clean history-only Windows application, QA reports, longer production acceptance | P7 durable product |

P3 voice and P4 media research can proceed in parallel AFTER approved Script, but P5 must freeze a final approved WAV and approved asset version set. No phase may call an LLM endpoint, or simulate absent media/time anchors. Stage "implemented" requires exact branch SHA and demonstrated tests.

## Specific Codex instructions

- P0 immediately: [Codex P0 prompt](../CODEX_P0_ON_NEW_MAIN_PROMPT.md).
- P1 immediately after P0 passes: [Codex P1 hard-cut prompt](../CODEX_P1_STRICT_CLEAN_CUT_PROMPT.md).
- P2–P8: give Codex the **exact phase document above** with this index and v3 authority; require a named phase-only PR, precise file ownership and test report. Do not let Codex silently start the next phase.
- For each PR: exact base/head SHA, changed-file ledger, canonical schemas/API examples, positive/negative tests actually run, Windows/Remotion/frame/audio evidence where applicable, security/source/rights audit, user-visible UI and an explicit PASS/PARTIAL/BLOCKED decision. Partial feature code must not be called "ready".

## Required product gates

G0 Repository identity / phase branch / no main push.
G1 No old YTB runtime/DB/data/source compatibility from P1 onward.
G2 No internal model/LLM APIs or automation of ChatGPT.
G3 Strict authoring files, true source lineage and immutable CAS publication.
G4 Real audio-aligned visual motions, approved images/maps/license provenance.
G5 One Remotion composition for Player and renderer; decoded FFprobe + keyframes.
G6 Windows process cancellation/recovery and durable publish fencing.
G7 Full product readiness and QC; **never a root MANIFEST.json check**.

All old generic v1/v2 plans on the previous feature branch or marked SUPERSEDED should be treated as historical context only. The release package may include domain-specific **ApprovedAssetManifest** and **RenderManifest** project records, not a root repository integrity manifest file.
