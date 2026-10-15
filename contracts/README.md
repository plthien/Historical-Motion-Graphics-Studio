# Contract Authority

Active contracts are organized by ownership:

- `contracts/schemas/core/`: external prompt/import envelopes and shared artifact primitives.
- `contracts/schemas/platform/`: Channel, Project, Topic Ideas, combined Script & Analysis, and Media contracts.
- `contracts/schemas/v4/`: deterministic Style Bible, Visual References, full-Script Scene Breakdown projections, internal Prompt Bundle, and production contracts.
- `contracts/schemas/jobs/`: durable media/voice/render worker envelopes.
- `contracts/schemas/settings/`: active Media, TTS, and Render settings only.
- `contracts/prompts/platform/`: external ChatGPT prompts for Topic Ideas and combined Script & Analysis.
- `contracts/prompts/v4/`: external ChatGPT prompt for full-Script Scene Breakdown.
- `contracts/openapi/youtube_ai_studio_api.openapi.yaml`: static API authority regenerated from runtime.

Scene Breakdown raw output and Visual Plan model one common image design per beat. Prompt Bundle 3.0 contains one item per beat and no exclusion contract. Scene Asset Manifest 3.0 models one media-provider generation/review lifecycle. Assembly and render contracts do not contain asset classification.

Prompt Bundle remains an immutable backend contract and immediate Scene Assets input, but it is not a visible Studio stage. Scene Breakdown approval compiles and publishes it atomically with approved Scene Plan and Visual Plan.

Raw ChatGPT responses remain untrusted until strict parse, schema, semantic, lineage, and source validation complete. Controlled objects reject unknown properties unless a contract explicitly models extensible metadata.
Pending external-authoring prompt reservations are durable server state: the exact prompt package and reserved Artifact IDs are restored after browser reload until import consumption or explicit rebuild. Reload never regenerates a prompt.

There is no internal LLM provider contract, research contract, compatibility schema tree, fallback reader, version-dispatch contract, local graphic renderer contract, or asset-classification contract.

Run `python tools/validate_contracts.py` after changing any contract or API path.
