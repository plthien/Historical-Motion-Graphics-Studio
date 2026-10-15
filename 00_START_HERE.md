# Historical Motion Graphics Studio — Start Here (developer entrypoint)

**This repository is being converted from the donor YouTube AI Studio source code to one clean Historical Motion Graphics Studio.** The current baseline code is still the donor until P1 passes. Do not interpret that donor implementation as a supported alternative flow or as coding authority.

## MANDATORY FIRST READ for every coding model / Codex / reviewer

1. [AGENTS.md](AGENTS.md) (root agent entrypoint)
2. [docs/history/CODING_RULES.md](docs/history/CODING_RULES.md) — **ONLY active coding rules**
3. [docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md](docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md)
4. [docs/history/phases/README.md](docs/history/phases/README.md) and the ONE approved P0–P8 phase specification
5. [.agents/skills/README.md](.agents/skills/README.md) and the relevant **NEW** `.agents/skills/*/SKILL.md` files
6. [docs/history/agents/README.md](docs/history/agents/README.md) and matching agent role files
7. Only then inspect donor source code, stage contracts and tests. They may inform extraction, not new coding rules.

**DO NOT use any YouTube Studio/YTB V4 coding rule, old developer prompt, cached skill, historical source-of-truth guide or deleted `docs/32_CODING_RULES.md`.** The old coding rules are removed. No duplicate rule authority or fallback.

## Target production flow

Channel & Project (NEW empty history database) → External ChatGPT Topic JSON import → External ChatGPT Script & Historical Analysis JSON import → Kokoro/ElevenLabs Final Voice + real forced alignment → External ChatGPT historical asset research JSON + source/rights review → External ChatGPT Visual Director multilayer JSON → deterministic word-cued Motion Timeline → shared Remotion Player/export composition → supervised MP4 + QC.

- **NO LLM API, no local LLM, no automatic ChatGPT browser use**. The operator manually runs prompts externally and imports strict downloaded JSON.
- **P1 complete clean-cut**: no old YTB product route, Scene/Beat pipeline, database/schema/migration, provider, UI stage or compatibility support. No YTB user data imported; old files on user machine remain untouched.
- **Remotion is sole video compositor**, non-LLM TTS/FFprobe allowed as scoped services.
- **NO root MANIFEST.json** or manifest generation/check/autopush workflow.
- Never push to main, auto-merge, close unrelated PRs or change repository default branch.

## Current implementation and how to proceed

At this migration stage, the branch containing the detailed plan is a **documentation/governance proposal**, not an implemented fully working history video app. P0 validates the renderer in isolation; P1 removes ALL legacy implementation atomically and boots the fresh historical skeleton; P2–P8 add the new features and verify actual videos. Only tests run on the exact candidate SHA count as PASS.

The donor YouTube workflow described in old source/readmes is historical reference only. Do not run the donor `start.bat` as proof of Historical Studio readiness until P1 rewrites and verifies it.

For coding task assignment: [Codex P0 prompt](docs/history/CODEX_P0_ON_NEW_MAIN_PROMPT.md), [Codex P1 prompt](docs/history/CODEX_P1_STRICT_CLEAN_CUT_PROMPT.md) and [detailed phases](docs/history/phases/README.md).
