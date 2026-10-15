---
name: implementation-audit
description: Self-review each History Studio phase implementation against strict source ownership, no-LLM/no-legacy, source/rights, schema/UI/API and actual Remotion/Windows test gates.
---

# Implementation Audit Skill — NEW HISTORY RULES ONLY

## Mandatory use
Run after EVERY phase/PR and again after fixes. Authority: `AGENTS.md` → `docs/history/CODING_RULES.md` → `HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md` → exact `phases/P<n>_*.md`. Any old YTB rule, old coding guide or cached skill is NOT normative.

## Audit steps
1. **Repo/commit:** verify target repository, explicit main/staging base, phase branch HEAD, no main push, no silent PR merge or unrequested operation. Capture changed/new/deleted files and transitive import ownership.
2. **Rule/skill closure:** ensure AGENTS and current History rules/skills loaded; deleted `docs/32_CODING_RULES.md`, root MANIFEST.json and its generator/autopush/check remain absent, active entrypoints route only NEW guidance.
3. **Legacy closure:** after P1, static and runtime checks show zero YTB stage/route/provider/module/DB/schema/data path, no Scene/Beat/PromptBundle compatibility; old user DB original hash/mtime unchanged.
4. **LLM closure:** inspect dependency/config, request paths and retries for hidden model SDK/network, local LLM/chat UI automation. Manual external prompt and JSON import is the ONLY authoring path; TTS and archive metadata explicitly separate.
5. **Contracts/data:** JSON strict duplicate keys/unknowns/lineage, validate-only read-only, CAS concurrency and immutable parents; history claims/time/maps/source-license review; content hashed by bytes.
6. **Audio/timeline:** final approved real WAV+word alignment IDs, integer frame compiler, continuity, multiple animated layers, approved media, voice invalidation.
7. **Preview/render:** same composition in Player and Node; actual H.264 sample/keyframes/FFprobe/audio QC and Windows owned process crash/cancel if phase requires. No claiming PASS because old CI is green.
8. **UI:** controls backed by API, review/errors/status visible, stage readiness, Cockpit theme and project selection; screenshots and accessibility checks.
9. **Self-review:** identify meaningful code defects, fix only affected portion, rerun relevant tests on NEW head SHA; describe remaining blocking risks and exact evidence.

## Required review output
```text
Repository / Phase / base + head SHA
Touched, extracted, retired files and public contract diffs
New coding-rules and NEW skills compliance
LLM/API closure + legacy/DB/data closure
Validation, security, asset rights, actual historical review
UI/browser screenshot; voice/FFprobe/render/Windows evidence
Tests: command => PASS | FAIL | NOT RUN, exact linked job/artifact
Unresolved defects and targeted next actions
Final verdict: PASS | PARTIAL | BLOCKED
```

Only PASS when every mandatory phase gate is independently verified and all build/test evidence applies to the current candidate commit. Otherwise explicitly PARTIAL or BLOCKED. No automatic merge, no root release manifest.
