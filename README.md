# YouTube AI Studio V3

A local-first, schema-driven YouTube production application. Creative authoring is performed in external ChatGPT conversations; the application builds authoritative prompts that request named JSON files, imports strict JSON results, publishes canonical artifacts, and runs the deterministic/media production pipeline.

## Active pipeline

```text
Channel → Project → Channel DNA
→ Topic Ideas prompt/import
→ combined Script & Analysis prompt/import
→ Voice from Script only
  → approved Narration Audio + NarrationWordTimingMap
→ deterministic Style Bible
→ Visual References V2 durable generation/review
  → immutable Visual Reference Set + Approval Set publication snapshots
→ backend deterministic micro-Scene + legal Beat-boundary planning after approved Voice
→ Scene Breakdown Step 1 partition/semantic-state prompt/import
  → exact backend materialization + locked Scene Plan candidate
→ Scene Breakdown Step 2 Visual prompt/import
  → draft Scene Plan 2.0 + Visual Plan 3.0
→ Scene Breakdown approval atomically publishes:
  approved Scene Plan 2.0 + approved Visual Plan 3.0 + approved Prompt Bundle 3.0 + BeatTimingMap
→ Scene Assets V2 durable generation/review
  → approved Scene Asset Manifest 4.0 publication snapshot
→ Assembly 3.0 and subtitles
→ Render through the Dedicated Durable Render Host
→ Final Video
```

Prompt Bundle remains an immutable backend artifact and immediate Scene Assets input, but it is compiled automatically during Scene Breakdown approval and is hidden from Studio navigation. Every visual beat follows the same media-provider image generation and candidate-review path.

Scene Assets V2, Visual References V2, and Voice use separate domain working-state tables and one shared Durable Media Workflow Host for process scheduling. Render uses its own SQLite-backed `RenderOperation` scheduling/lease state and a separate Dedicated Durable Render Host so long-running FFmpeg/FFprobe work cannot starve the shared media domains. All four durable execution domains survive API/UI/host restarts through persisted state. Redis, RQ, and a platform Backend Worker are not active runtime dependencies.

The backend makes no internal LLM or web-research calls. It does not expose LLM provider settings, research jobs, creative generation jobs, or compatibility routes.

## Repository layout

- `app/modules/platform/`: Channel, Project, Topic Ideas import, combined Script & Analysis import, and media foundations.
- `app/modules/media/durable_workflow/`: neutral durable coordinator/provider/host mechanics shared by Scene Assets V2, Visual References V2, and Voice without shared physical workflow tables.
- `app/modules/v4/`: deterministic creative projections, Visual References V2, Scene Assets V2, production stages, durable Render execution engine, and Windows process ownership helpers.
- `app/render_coordinator_runner.py`: Dedicated Durable Render Host coordinator and fenced SQLite execution ownership.
- `app/core/external_authoring.py`: shared prompt packaging, reserved Artifact ID extraction, strict JSON parsing, true upstream-source checks, and validation response helpers.
- `app/services/external_authoring_reservations.py`: minimal pending/consumed database reservations for reload-safe JSON import.
- `contracts/prompts/`: copyable external ChatGPT prompt authority.
- `contracts/schemas/`: strict import, canonical artifact, settings, job, and production contracts.
- `src/`: React Studio and prompt/import workspaces.
- `docs/99_SOURCE_OF_TRUTH.md`: current runtime authority.

## Windows quick start

Extract the release to a short path such as `D:\yas3`. The release ZIP uses the short root folder `yas3` to remain compatible with Windows Explorer path limits.

Double-click `start.bat`. On the first run, the launcher automatically:

- creates `.venv` with Python 3.12 x64 when it is missing;
- installs pinned Python dependencies from `requirements.txt`;
- installs pinned frontend dependencies with `npm ci`;
- records dependency fingerprints and skips installation on later runs until `requirements.txt` or `package-lock.json` changes.

Python 3.12 x64 and Node.js LTS must already be installed. FFmpeg and FFprobe must be resolvable by the Dedicated Durable Render launcher. Redis/RQ is not installed, required, or started. After setup, the launcher opens four terminals:

- Backend API: `http://127.0.0.1:8000`
- Durable Media Workflow Host for Scene Assets V2 + Visual References V2 + Voice
- Dedicated Durable Render Host for SQLite-scheduled FFmpeg/FFprobe execution
- Frontend: `http://127.0.0.1:5173`

The Render host claims due operations under durable owner/token/expiry fencing. On Windows, child media processes are assigned to an owned Job Object with kill-on-close semantics, so a host crash cannot leave an unfenced FFmpeg process tree running. `start.bat` always uses `.venv\Scripts\python.exe`; it does not depend on whichever Python happens to be active in the terminal.

## Development setup

Use a fresh Python 3.12 x64 virtual environment, then install `requirements.txt`. Run:

```bash
alembic upgrade head
python tools/validate_contracts.py
python tools/audit_no_redis_rq.py
pytest tests/platform tests/v4
```

Frontend commands are defined in `package.json`. Development databases and generated artifacts are disposable.

## Voice provider configuration

Voice depends on the approved Script, not Scene Plan. Voice and Style Bible / Visual References may proceed in parallel after Script & Analysis, while Scene Breakdown requires an approved Voice pair.

Production Voice generation supports two explicit providers:

- `elevenlabs`: uses the Channel-owned Professional Voice Clone profile and one preset:
  - `economy -> eleven_flash_v2_5`
  - `quality_cloned_voice -> eleven_multilingual_v2`
- `kokoro_local`: uses the local Kokoro runtime and an explicit voice ID from the runtime catalog. Kokoro voices are not stored in the Channel Voice Profile.

Final paced WAV is audio truth for every generation provider. ElevenLabs Forced Alignment on the final paced WAV is the only timing truth; it is not provider-selectable. Draft Voice candidates do not move current Project Voice pointers and invalidate nothing downstream; Voice approval is the source-of-truth cutover and downstream invalidation point. BeatTimingMap is Scene-owned and published on Scene approval, and Assembly consumes BeatTimingMap rather than deriving Beat timing itself.

## Quality gates

- strict JSON parsing with duplicate-key rejection;
- preallocated reserved Artifact IDs, frozen Topic avoid lists, reload-safe upload, one-time reservation consumption, and true upstream-source rejection;
- full Topic review fields with editable Script POV/avoid handoff, pacing-mode recommendation, user-overridable FAST/MEDIUM/SLOW video pacing, per-section narration WPM, and duration-aware section planning;
- strict JSON Schema and Pydantic validation;
- immutable artifact hashes, lineage, and paired CAS publication;
- deterministic Style Bible and automatic all-beat Prompt Bundle compilation;
- durable Scene Assets/Visual References/Voice ownership and recovery regression;
- dedicated SQLite Durable Render ownership, execution fencing, Final Video CAS/lineage regression, and Windows Job Object lifecycle coverage;
- production-stage regression through Scene Assets, Voice, Assembly, and Render;
- static/runtime OpenAPI parity;
- closure scans proving zero internal LLM/research runtime and zero active Redis/RQ/queue-dispatch runtime;
- Linux backend + frontend candidate gates and the Windows Render candidate gate on the same exact HEAD;
- deterministic manifest and archive audit.

Read `00_START_HERE.md` before every implementation task.
# ytb-ai-studio
