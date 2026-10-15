# Transfer record — 2026-10-15

User-directed relocation of **all 16 changed file contents** from the unmerged Draft PR #2 of `plthien/2D-Animation-Studio` (source head `4e505976fe9d9a6a5478341cef3d99003389b4ac`) into the **feature branch only** of `plthien/Historical-Motion-Graphics-Studio`.

The 15 added project files retain their original paths and Git blob contents. The legacy root `MANIFEST.json` is preserved byte-for-byte at `archive/source-pr2/MANIFEST.json` because it described the old YouTube/2D studio repository and must not be used as the root manifest of the historical studio. This archive is provenance only.

CRITICAL: Documents under `docs/implementation/` may mention `plthien/2D-Animation-Studio`, old root app/, src/, legacy project layout, PR #1 Spine or PR #2 as their original implementation target. That is outdated after transfer. Before implementation, rebase/supersede architecture & file ownership instructions for this EMPTY-SEED, standalone repository. Do not copy old V4 services indiscriminately, and do not run Codex on the old repository. The conceptual P0–P8 plan remains useful, but repository-target/file-path assumptions require an explicit revision first.

No user-authorized write to any repo's `main` is allowed. All follow-up edits must target a named feature branch, with review before any merge.
