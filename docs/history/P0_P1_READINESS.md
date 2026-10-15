# P0 — source-backed donor audit / P1 readiness

Source: `main` at `0119572d651cb2b1902aea21b6267cca9809a940`.
Executable audit: `python tools/history_p0_source_audit.py`.
Machine-readable output: `source-ownership-ledger.json` (`history_source_ownership_ledger.v2`).

## Inventoried donor surface

- **757 tracked files** in app/, src/, contracts/, alembic/, tests/, scripts/, tools/ and GitHub workflows
- **144 static API method/path decorators**, before router-prefix expansion and runtime shadow-precedence; these are not 144 distinct full OpenAPI paths
- **29 detected SQLAlchemy `__tablename__` declarations** across donor models
- **31 old Alembic revision scripts**, all retired in P1, with a fresh History initial migration
- **8 YTB UI labels**: Topic Ideas, Script & Analysis, Style Bible, Voice, Scene Breakdown, Scene Assets, Assembly, Render
- Legacy Windows launchers in `scripts/windows/`, V4/media host workers, FFmpeg still-asset assembly, voice infrastructure

The JSON ledger records every path, current owner/responsibility, EXTRACT/REWRITE/DELETE, new owner, direct internal imports, transitive static import closure, matching candidate regression tests, extraction preconditions and P1 risks. Import edges are derived with Python AST and static JavaScript/TypeScript module imports. **Static analysis does not establish runtime/dynamic import completeness or actual test coverage.**

## P1 prerequisites — NOT AUTHORIZED BY P0

1. Independently prove dynamic API router mounting, OpenAPI closure, module graph and old routes.
2. Extract neutral duplicate-key JSON parser, hash/CAS and ownership guards, Kokoro/ElevenLabs/true final-WAV alignment and Win32 process ownership into new history-only modules with new tests.
3. Replace YTB UI/API/DB/migration/stages atomically in P1, not gradually or as a second mode.
4. Prove old files on user's machine are not opened, modified, imported or migrated.
5. Verify launchers and workers no longer start YTB processes; new history root and initial migration are clean.

P0 does **not** delete source/data, alter the YTB runtime, add new production APIs or implement P1.
