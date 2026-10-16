# Historical Motion Graphics Studio — Start Here

## Required governance reading order

1. [AGENTS.md](AGENTS.md) — active root authority
2. [New History Coding Rules](docs/history/CODING_RULES.md) — **ONLY** coding rulebook
3. [Strict Clean-Cut Plan v3](docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md)
4. [History phase plans P0–P8](docs/history/phases/README.md), including [P1 acceptance gates](docs/history/P1_ACCEPTANCE_GATES.md)
5. [NEW skills](.agents/skills/README.md) under \`.agents/skills/\` only
6. [NEW agent roles](docs/history/agents/README.md)
7. Actual code and tests for the authorized phase

Do not follow donor YouTube Studio instructions or old Scene/Beat/manifest/LLM integration plans. They are not current authority.

## P1 run

The current tool is a **History-only Channel and Project foundation**, not a finished renderer. Install Python 3.12 and Node 22, then run **start.bat** on Windows or use commands in [README.md](README.md). New dedicated SQLite is \`history_data/history_app.db\`. Old YTB databases and projects are not opened/migrated/deleted. FastAPI port 8000, Vite port 5173. Only Channel/Project management and genuine readiness statuses are operational; seven other stage panels remain disabled until their owner phases.

P0's silent Remotion experiment remains under \`experiments/remotion-history-poc/\`. Video reference comparison belongs to P6; it must not become a P1 requirement.

**No LLM API. No legacy compatibility. No root MANIFEST.json. Never push directly to main or merge automatically.**
