# Coding agent roles — Historical Motion Graphics Studio

**Read AGENTS.md and docs/history/CODING_RULES.md first.** The project has ONE coding rulebook and ONLY the new repo-specific skill directory `.agents/skills/`. These role descriptions route work to a reviewer focus; they are not a permission system, internal LLM service or substitute for phase acceptance.

| Role file | Responsibility | Mandatory skill |
|---|---|---|
| [Architecture](architecture-agent.md) | Strict migration, contracts, module ownership and no-legacy closure | clean-cut-migration |
| [Backend](backend-agent.md) | FastAPI, storage/CAS, external JSON, durable jobs | external-chatgpt-authoring |
| [Frontend](frontend-agent.md) | Cockpit workflow, strict import/review and timeline Player | remotion-motion-graphics |
| [Remotion](remotion-agent.md) | Compositions, frames, effects, browser/render parity | remotion-motion-graphics |
| [Historical Assets](historical-assets-agent.md) | Archive provenance/license/period/context | historical-asset-research |
| [Voice](voice-agent.md) | Kokoro/ElevenLabs voice timing, process identity/lineage | voice-alignment |
| [QA/Security](qa-audit-agent.md) | Negative tests, no-LLM/no-legacy, Windows/FFprobe gates | implementation-audit |

**Phase routing:** P0 Architecture + Remotion + QA; P1 Architecture + Backend + Frontend + QA; P2 Backend + Frontend + QA; P3 Voice + Backend + QA; P4 Historical Assets + Backend + Frontend + QA; P5 Backend + Remotion + QA; P6 Remotion + Frontend + Voice + QA; P7 Frontend + Backend + Remotion + QA; P8 QA + all affected specialties.

When a role applies, read its role .md and the associated NEW skill completely; never read old YTB developer/authoring prompts as active role instructions. Codex remains accountable for tests and PR report even when reviewing through multiple roles.
