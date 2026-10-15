---
name: clean-cut-migration
description: Atomic source-level conversion from the donor YouTube Studio into a single history-only app with fresh SQLite, routes and UI, without compatibility or old data.
---

# Clean-Cut Migration Skill — NEW HISTORY RULES ONLY

## Scope and required authority
Use P0 donor inventory and P1 strict cutover. Read root `AGENTS.md`, `docs/history/CODING_RULES.md` and `docs/history/phases/P1_ATOMIC_CLEAN_CUT.md` first. Ignore and do not execute old `docs/32_CODING_RULES.md` or any legacy V4 instructions. This skill owns source cleanup, NOT permission to delete old user-machine data or write to main.

## Preconditions and input
- Verify repository and explicit current `main` SHA, target feature branch and user-authorized phase.
- Collect all source paths under app/src/contracts/tests/alembic/scripts/tools/CI, transitive imports, actual API endpoints, DB tables and UI stages. Produce machine-readable ledger with old owner, new owner, strategy EXTRACT/REWRITE/DELETE, tests and dependency order.
- P0 proof separately tested; P1 must deliver an *atomic*, history-only runnable product (disabled unfinished history stages permitted), not two operational modes.

## Procedure
1. Freeze history-only seven stage keys, fresh SQLite schema + first history migration and storage roots before rewriting route/UI heads.
2. Extract individually verified neutral functions: duplicate-key JSON parser, stable content hashes, CAS, bounded Windows process guard and usable TTS helpers. Place in new history-owned packages and port tests; no old `app.modules.v4` imports.
3. Replace app/main.py and API routes, frontend stage views/client DTOs and launch scripts together; then remove legacy scene/beat/visual references/scene asset/prompt bundle/slideshow implementations, migrations, provider workers, settings and tests from product.
4. Reject any old YTB SQLite/data path without read/migration/writes: typed `HISTORY_LEGACY_STORAGE_UNSUPPORTED`; verify old DB hash+mtime unchanged. Fresh installation begins EMPTY with new history schema marker.
5. Remove generated/user data from release staging by exclusion, never by silently deleting user directories; do not migrate old projects. Root release `MANIFEST.json` remains permanently removed—do NOT regenerate it.
6. Run static AST/import and OpenAPI route closure, UI route/stage closure, migration/table scan, CI/Windows launcher checks, fresh create Channel/Project restart test, no-LLM scan and security tests.

## Required outputs
- Exact source ledger, before/after import graph, new schema names/migration, typed route contract and seven stages.
- Evidence: clean DB startup/restart, legacy DB fail-closed without modifications, no old import/route/panel/schema/manifest generator, frontend+backend build, Windows smoke, exact SHA.
- If any old production support remains: **BLOCKED**, never mark P1 PASS or start P2.

## Forbidden
No legacy API aliases, hidden modes, migrations/backfills, compatibility loader, old YTB assets, old manifest refresh, old coding rules/skill instructions, internal LLM call, direct push to main.
