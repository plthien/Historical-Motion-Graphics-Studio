# P0 final acceptance scope and self-review

Repository: `plthien/Historical-Motion-Graphics-Studio`. Base `main` at
`0119572d651cb2b1902aea21b6267cca9809a940`; Draft PR #2 on
`feat/p0-history-remotion-feasibility`. Changes limited to P0 proof,
ledger, review docs and focused regression tests.

## Specific repair — final P0 residuals

- **A Media decoding:** byte signature, extension/codec agreement, size and
  image dimensions or audio duration enforced before full FFmpeg decode, with
  bounded child-process execution and known public-root/symlink checks. POC
  audio remains null. Invalid data fails before the real renderer starts.
- **B Plan authority:** frozen canonical `motionPlan` imported by both
  `Root.jsx` and `preview.jsx`; renderer rejects divergent supplied plans
  and rechecks the canonical JSON snapshot after awaited preflight. A digest
  identifies the approved render input. P0 does not accept external plans.
- **C README:** isolated POC scope, Windows steps, FFmpeg/Chromium tools, CLI,
  test and artifact guidance under `experiments/remotion-history-poc/README.md`.
- **D Documents:** frozen donor ledger includes all 839 tracked files,
  53 `docs/` documents and seven History skill entries. Retired YTB
  documents are explicitly non-authoritative. No P1 deletion performed.
- **E Editorial:** review exact rendered MP4, chronological frame contact
  sheet and Player/MP4 parity; the external benchmark was not supplied:
  `REFERENCE_STYLE_COMPARISON_NOT_VERIFIED`.

## Unchanged P0 safety boundaries

No LLM SDK/API, legacy DB/data migration, root `MANIFEST.json` or
replacement YTB runtime. Browser/MP4 share the same composition.
P0 still uses a silent schematic/map and an explicit archival placeholder.

## Gates

The exact final candidate must pass: `npm ci`, all POC and History tests,
Vite build, real Player screenshots, 60s Remotion MP4 on Linux and Windows,
FFprobe full decode, native-resolution pixel-parity and governance/ledger checks.
Previous HEAD's green CI is historical only, not evidence for a new HEAD.

**Reference editorial comparison remains NOT VERIFIED without the actual
reference video. Overall P0 may remain PARTIAL even when technical CI passes.**
