# P0 donor ownership / P1 readiness — source snapshot

Frozen donor SHA: `0119572d651cb2b1902aea21b6267cca9809a940`.

## Verified immutable inventory

- **839** tracked donor files inventoried across app/, src/, tests/, contracts/, alembic/, scripts/, tools/, kokoro/, .github/, .agents/, docs/ and relevant root files.
- **53** tracked `docs/` documents classified: 23 preserve (new History governance/plans), 24 delete (obsolete YTB authority), 5 archive (non-authoritative historical design), 1 rewrite (old docs index).
- `AGENTS.md` and seven NEW `.agents/skills/` entries are preserved as new authority; they are never treated as obsolete YouTube coding instructions.
- 144 statically declared router endpoints, 29 SQLAlchemy tables and 31 legacy migration files were identified. Router method/path declarations are **not** proof of final mounted OpenAPI route counts.
- The file-by-file `docs/history/source-ownership-ledger.json` records decisions, static direct/transitive imports, candidate tests and P1 cut risks; docs additionally record authority and references.
- Source-derived data is read only with `git show <donor SHA>:<path>`. Current-branch P0 documents are separately identified as candidate outputs, **not represented as files existing in the donor snapshot**.
- The `--verify` command asserts byte-identical deterministic output and rejects stale/omitted documentation.

## P1 dependencies (not implemented or authorized here)

1. Extract only independently tested, neutral JSON, CAS/hash, Kokoro/ElevenLabs, Windows Job/process and durable-workflow algorithms.
2. Establish P1-specific dynamic import/route ownership and runtime test closure; static source graph does not replace it.
3. Atomically replace all YTB runtime, UI, database/migrations, legacy asset/slideshow paths and launcher with a single new History Studio.
4. Never load/migrate old user SQLite, WAV/media/assets or touch files under former application roots; preserve old local user data outside history storage.
5. Remove old YTB coding/authoring documents and obsolete workflow links from active History UI and shipped documentation, preserving new History rules and phase guides.

**P1 is not ready to execute based solely on this ledger; P0 PR must not implement P1.**
