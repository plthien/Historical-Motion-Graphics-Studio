# Historical Motion Graphics Studio — P1 Foundation

**This repository contains one History-only application.** The former YouTube Studio product was removed at source level in P1. Historical Motion Graphics Studio is a locally hosted application for planning sourced historical motion videos. **P1 supports real Channel and Project persistence and displays seven honest blocked stages, not yet a completed video production tool.**

**Authority:** [AGENTS.md](AGENTS.md) → [new History Coding Rules](docs/history/CODING_RULES.md) → [Strict Clean-Cut Plan v3](docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md) → [P0–P8](docs/history/phases/README.md) → [new skills](.agents/skills/README.md) → [new agents](docs/history/agents/README.md). Old YTB guidance is not permitted authority.

## Windows quick start

Install Python **3.12 x64**, Node.js **22**, npm and Git. Double-click **start.bat**. The launcher creates a dedicated virtual environment, installs pinned Python dependencies and reproducible frontend dependencies, verifies **fresh isolated History SQLite**, starts only FastAPI and Vite, opens http://127.0.0.1:5173 and shuts down the processes it owns when terminated.

Manual developer mode from the root:

\`\`\`powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
\`\`\`

In another terminal:

\`\`\`powershell
npm run dev
\`\`\`

The frontend is on **http://127.0.0.1:5173**, the API is on **http://127.0.0.1:8000** and FastAPI documents are at **http://127.0.0.1:8000/docs**. The Vite dev server proxies /api and /health to the local API.

Default storage: **\`history_data/history_app.db\`** with immutable artifacts in \`history_data/artifacts/\`. An optional \`HISTORY_DB_PATH\` configures a **History-only DB**, not an old YTB database. Existing foreign/legacy SQLite files fail closed with \`HISTORY_LEGACY_STORAGE_UNSUPPORTED\`, without migration or mutation. Never point to an old application database, and never delete old user data.

## Working in P1

Create/list/select channels and projects, reopen an existing project after restart, inspect live status for exactly seven stages:

1. Topic Ideas — P2
2. Script & Historical Analysis — P2
3. Voice & Alignment — P3
4. Historical Assets — P4
5. Visual Director — P5
6. Motion Timeline — later phase
7. Render & Delivery — later phase

These seven stages are **not functional video production services in P1**. Their panels display \`NOT_IMPLEMENTED_P*\` from real API status and disable unavailable actions. No fake generation button.

## API and testing

Endpoints: \`GET /health\`, \`GET/POST /api/history/channels\`, \`GET/POST /api/history/projects\`, \`GET /api/history/projects/{project_id}\`, \`GET /api/history/projects/{project_id}/status\`. The persisted data model contains History channels/projects, stage heads, immutable artifact metadata and authoring reservations. Basic strict JSON parsing and transactional CAS utilities are independently tested; the authoring workflow itself begins in P2.

\`\`\`powershell
.venv\Scripts\python.exe -m pytest -q tests/history
npm test
npm run build
\`\`\`

CI runs the History backend/frontend, runtime OpenAPI and legacy-data-safety checks, P0 Remotion tests and Windows startup/restart smoke. See \`.github/workflows/history-p1.yml\`.

## Boundaries

- **No LLM API, local LLM or automatic ChatGPT browser.** Future authoring is manual External ChatGPT prompt/export/import.
- **No legacy YTB compatibility, Scene/Beat/PromptBundle pipelines, data import, worker hosts or old SQLite migration chain.**
- **No production TTS, forced alignment, archives, Motion Timeline editing or Remotion render API in P1**. The [P0 Remotion proof](experiments/remotion-history-poc/README.md) remains isolated; production video implementation is P6/P7.
- **No root MANIFEST.json, generator or verification workflow.**
- Future visual reference quality gate and historical attribution/rights are specified in the [P6 phase plan](docs/history/phases/P6_PRODUCTION_REMOTION_VIDEO.md).
