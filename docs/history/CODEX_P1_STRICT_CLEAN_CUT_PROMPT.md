
# Codex — P1 STRICT HARD CUTOVER (run only after accepted P0)

## Scope and repository safety

Implement **P1 only**, as specified by docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md.
Repository must be plthien/Historical-Motion-Graphics-Studio.
Branch MUST be a new feature branch from exact accepted main/staging SHA, NOT the old feature prototype, and never main directly. Verify repo, main SHA, working tree and read architecture docs before any write. No automatic merge or changes to GitHub settings.

P1 is an **ATOMIC replacement** of YouTube AI Studio production with a clean Historical Motion Graphics Studio skeleton. Do NOT postpone any legacy delete to P8; P2 must see no production legacy routes, schema, data, workers, API fields, UI panels or files.

## Non-negotiable decisions

1. No LLM API or ChatGPT automation; External ChatGPT prompt/file import only (authoring features themselves are P2/P4/P5).
2. A new empty History-only SQLite database and migration root; NEVER migrate, import or open an old YTB database, and NEVER include old images, project data, media cache or old database in the new release.
3. Extract ONLY neutral algorithms and proven infrastructure (JSON parser, CAS/artifact hashing, bounded process, Voice primitives), rewrite domain ownership, re-test independently. Do not retain app/modules/v4, legacy routers or legacy table names as compatibility wrappers.
4. Maintain Cockpit UX styling and navigation components, but present only 7 History stages with genuine readiness and explicit 'Not implemented' gates until P2–P8. No enabled dead-end buttons, simulated services or false claims.
5. The history Remotion compositor is a different engine; old FFmpeg still-image generation/scene/beat/prompt bundle pipeline is removed. P1 need not produce final video; P0 proof is separate.
6. DO NOT delete actual existing user DB/data from outside the new history root. Old files on original user machines remain untouched, unsupported and unreferenced.

## Mandatory implementation checklist

A. Generate machine-readable source ledger from exact accepted main commit including all app/, src/, contracts/, alembic/, tests/, docs/, scripts/, and launcher entries. Every path assigned KEEP (generic code, NEW owner), EXTRACT, REWRITE or DELETE. Validate all transitive imports before removal.

B. New Python History app: single app/main.py (or equivalent single history-only entrypoint); only history Project/Channel and health/storage routes. Remove import+mount of YTB V4, Scene Breakdown, Scene Asset and Visual Reference routers and their schemas. New OpenAPI must not include any prior YouTube route family.

C. New history domain models and fresh Alembic history chain (starting at 001_history_initial, with no old 001..031 files). Default database file and storage root distinct from anything in YTB. Fail early with HISTORY_LEGACY_STORAGE_UNSUPPORTED on old YTB schema marker/tables/paths, WITHOUT writing or auto-upgrading old DB. Persistent fresh Channel/Project/StageHead/Artifact/Reservation basics work and CAS tests pass.

D. Port neutral JSON validation/reservation/immutable artifact/CAS/process guards to new module ownership and adapter-free APIs. Do not preserve old Stage contracts through a renamed wrapper. Tests prove duplicate JSON keys rejected, validate-only no writes, cross-project/stale input and race guard; no model provider configuration.

E. Replace src/pages/StudioPage.tsx + stage types with seven history-only stage routes and initial placeholder readiness states; preserve CockpitShell, GlobalSidebar, PageHeader, select Channel/Project, cards, dark UI and error banners. Remove SceneAssetsPanel/SceneBreakdownPanel/AssemblyPanel, old old API client types and V4 selectors from **production import graph**, and delete retired source and tests. Visual skeleton is honest: unimplemented phases disabled with explanatory tooltips/state.

F. Eliminate source-level retired YTB implementation: app/modules/v4 Scene/Beat/PromptBundle/SceneAssets/VisualReferences/Assembly/FFmpeg montage; obsolete workers, media provider/image-beat settings and transport; old YAML/JSON contracts and source-of-truth docs in shipped product; stale tests and CI checks for removed features. If a generic utility was imported by Voice, extract it to new neutral ownership before deletion and include migration tests.

G. Replace Windows launch commands and CI configuration. History startup should run the API and front end with accurate dependency checks; do not start old media/RQ/Redis or old FFmpeg Render host. Node Remotion render host remains a later phase; do not start a dummy host.

H. Audit packaging: do not include any old .db/.sqlite, .wav/.mp4/image generation outputs, project files, cached media, credentials/secrets. Dedicated POC fixture exceptions must be licensed and tiny. New README, 00_START_HERE and source-of-truth docs refer ONLY to history product. Never reintroduce a root MANIFEST.json or manifest generation/check step.

## Evidence gates — all required for PASS

1. Fresh, empty history DB bootstrap/migration, create Channel, create Project, reopen after process restart. Compare schema/table names against removed YTB tables and confirm zero overlap of YTB-specific tables/columns.
2. Inject a fixture of old YTB SQLite at startup and verify deterministic error HISTORY_LEGACY_STORAGE_UNSUPPORTED; compare DB file hash/mtime before and after: unchanged. No YTB data migrated.
3. Python import dependency scanner: no production import of app/modules/v4 nor legacy scene/beat/asset/assembly code; no active old route classes, DB models and provider settings.
4. Runtime OpenAPI and UI route/stage screenshots contain only history names; no old Scene Assets/Visual References/Beat/PromptBundle or YouTube branding.
5. Seven history stages visible, unfinished ones disabled with truthful blocker text; Channel/Project persists; basic shell UX at 1440x900 and 1920x1080.
6. Smoke tests, static closure scans, new migration schema tests, frontend TypeScript build, unit/negative tests, Windows launch/start/restart. Any unrun mandatory test = BLOCKED.
7. No LLM API: config/dependencies/code/network mocks show no internal model calls, even retries.
8. Source inventory exactly reconciles deleted/rewritten/extracted files; no unused legacy source root hidden in the application for compatibility.
9. PR diff, exact tested SHA and CI URLs, and a security/data-safety note: legacy data not copied into new app and existing user files elsewhere not touched.

If a gate fails, continue on the feature branch and mark PARTIAL/BLOCKED. Never claim acceptance and never restore the old YTB pipeline to unblock a test. No P2 until P1 receives explicit acceptance.
