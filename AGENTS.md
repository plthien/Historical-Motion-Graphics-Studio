# Historical Motion Graphics Studio — Agent Entry Point

**Repository identity:** `plthien/Historical-Motion-Graphics-Studio` ONLY. This file applies to the entire repository and to every coding agent, Codex session, reviewer and helper agent.

## Mandatory first-read order before ANY implementation or code review

1. This `AGENTS.md`.
2. **`docs/history/CODING_RULES.md` — the ONLY active coding-rules document.**
3. `docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md` — hard product boundary.
4. `docs/history/phases/README.md` and the ONE `P<n>_*.md` document for the requested phase.
5. The applicable **NEW skills under `.agents/skills/*/SKILL.md`** and roles under `docs/history/agents/`, as routed by `docs/history/agents/README.md`.
6. The actual source, schemas and focused tests for the authorized phase.

**ONLY the new History skills and the new History coding rules may direct implementation.** `docs/32_CODING_RULES.md` is deliberately deleted; DO NOT recreate it or treat cached copies as instructions. The former `docs/99_SOURCE_OF_TRUTH.md`, old YTB `docs/00_INDEX.md`, `README.md` YTB workflow sections, old `docs/10_IMPLEMENTATION_PHASE_PLAN.md`, legacy V4 readmes/prompts/fixtures, and old LLM provider-plan documents are DONOR/HISTORICAL CONTEXT ONLY, NEVER normative rules. Any contradiction is resolved by the new rules + v3 plan + phase contract, not by legacy text. If an old document asks you to follow old coding rules, ignore that instruction.

## Non-negotiable safety/product rules

- **No LLM API / SDK / internal model / local LLM / headless ChatGPT automation.** User manually runs External ChatGPT; Studio generates a prompt, imports a downloaded JSON file, validates/reviews/approves. Do not introduce hidden model retries or generate executable JS from model output.
- **Strict clean cut at P1:** new product contains NO old YTB Scene/Beat/VisualReference/SceneAssets/PromptBundle/FFmpeg slideshow paths, legacy API/UI/settings/providers, legacy migration chain or data/DB readers. Source donor code can be *extracted as neutral algorithms with NEW names and tests*; no compatibility wrapper.
- **Do not import, migrate, or open old YTB project DB/data.** New empty History SQLite + storage root; reject old DB without mutation. Never delete user files on their machine without explicit separate authorization.
- **Remotion** owns ALL motion composition and identical browser Player/export composition. Voice events are anchored to real approved final WAV word/phrase timing. Archive assets need evidence/date/license and truthful labels.
- **No root `MANIFEST.json`, no root release manifest generator/check, no manifest refresh/autopush workflow.** Do not conflate this prohibition with versioned history project asset/rights records.
- **Never push to `main`, never auto-merge PRs, rewrite default branch, close unrelated PRs or force-push without explicit instruction.** Before a write, verify repo owner/name, exact branch/ref and base commit. Work only on named feature branches; PR for review.

## Required agent routine and handoff

1. Announce authorized phase, exact source SHA, new branch, source-file ownership inventory and planned positive/negative tests.
2. Select ONLY applicable NEW skill(s); read each complete SKILL.md; use role guidance in `docs/history/agents/`. Old YTB "skills", prompt files, code comments and old rule text are NOT skill substitutes.
3. Implement one phase; if prerequisite is unmet, mark BLOCKED. P0 isolated proof, P1 atomic hard-cut, then P2–P8 per approved phase docs. Do not implement later phases by convenience.
4. Validate contract/DB/API/frontend/readiness parity, static legacy-closure and no-LLM guard; where relevant run browser Remotion/FFprobe/Windows process tests on exact code SHA. NEVER claim PASS on doc-only source review.
5. Self-review changed lines, source rights/history correctness, untouched user data and CI artifacts. Final report: base+head SHA, changed files, actual commands/results, unmet gates, PASS/PARTIAL/BLOCKED, PR link.

**Conflicting instruction in an old file? STOP following that old file's authority. Follow only the new History governance.**
