# External ChatGPT Authoring — Non-negotiable Product Contract

Status: **user-approved architecture constraint**, 2026-10-15. Applies to every phase of the Historical Motion Graphics Studio, from topic discovery through final render.

## Absolute constraint

**No LLM API calls anywhere inside the Studio.** Creative and analytical AI work must use an **external ChatGPT conversation**, like the current YouTube AI Studio prompt/export/import workflow. The application does not contact OpenAI/Anthropic/Gemini/other LLM providers, host a local LLM, run an agent, or automate ChatGPT through its UI. No LLM provider credentials, runtime LLM settings, internal research agent, prompt-execution queue or silent JSON "AI repair" calls may be introduced.

"AI Director" is a **prompt-and-import stage**, never a backend model provider. A prompt package is generated deterministically and displayed for the operator to copy/download; only an explicitly user-imported JSON file may become a candidate artifact.

## Canonical authoring flow

For every external-authoring stage:

1. **Build prompt package locally** from the selected Channel DNA, approved parent artifacts, precise user constraints, tool/visual vocabulary, an exact JSON Schema, reserved artifact IDs and a frozen source fingerprint. Include clear descriptions of inputs; never rely on opaque project names or implied conversation memories.
2. **User runs the prompt manually in external ChatGPT**, optionally asking ChatGPT to use web research. This interaction does not happen through the application or LLM APIs.
3. **ChatGPT produces a downloadable JSON file**, not a response pasted as unstructured prose. The user uploads that file into the Studio.
4. **Validate-only** parses strictly, rejects duplicate JSON keys and unknown fields, checks schema plus semantic constraints, source-of-truth parent identity/fingerprint, ownership, timing references and required evidence citations where applicable. Validation never writes or approves.
5. **Review candidate**; the user may reject, revise externally, or approve. Import revalidates everything. Approval publishes an immutable, content-addressed artifact using guarded compare-and-set; stale uploads must fail closed.
6. **Deterministic production** uses approved JSON, approved assets and the final voice/audio timing. No internal LLM fallback or automatic creative recomputation.

## External ChatGPT stages

| Stage | Prompt package input | User-imported JSON output |
|---|---|---|
| Topic Ideas | niche, audience, constraints and fresh-topic search instructions | `topic_ideas.v1` |
| Historical Script + Analysis | approved topic, historical uncertainty/claim rules, tone, duration | `script_analysis.v1` (including authoritative script text and verifiable claims) |
| Historical Asset Research Plan | script, claim IDs, visual requirements, allowable archives, licensing policy | `asset_research_plan.v1` (search terms, candidate URLs/citations and required verification, **not** downloaded or auto-approved assets) |
| Visual Director | approved script, actual voice timing map, reviewed evidence/assets, composition grammar and current layout constraints | `visual_motion_plan.v1` (visual setups/layers/motion events, word/phrase anchors; no arbitrary executable code) |
| Targeted Repairs | current failed artifact plus narrowly scoped validator/editorial errors | new candidate JSON referencing the prior approved parent; must not mutate locked successful portions unless explicitly authorized |

All output versions above are **proposed contracts**, not implemented runtime schemas in R0. Freeze the exact shapes during R1. There is no backend Topic Generator API, Script LLM API, Research LLM API or Visual Director LLM API.

## Allowed machine services (not LLM)

- Kokoro local TTS and optional ElevenLabs **voice synthesis/forced alignment** if separately configured and explicitly invoked.
- Metadata and asset retrieval from Wikimedia Commons, Library of Congress or other approved archives, subject to source/license/security checks. Optional non-LLM search/metadata APIs are allowed; downloaded resources are not automatically historical evidence.
- FFmpeg/FFprobe, image preprocessing, deterministic motion/timing compiler, Remotion Player/renderer, cache and local artifact database.
- Validators, schema generators, prompt assembly, hashes, safety checks, operations and preview/export.

Do not reclassify a text-generation or multimodal generative model endpoint as a harmless "media service" to bypass this constraint. External API costs and credentials for non-LLM services must remain optional, explicit and distinct.

## Safety, failure and test gates

- Import errors produce structured validation diagnostics and a **copyable targeted-repair prompt**; they never initiate an API retry to a model.
- Prompts must preserve frozen source IDs and exact script quotations, and distinguish historical fact from fictional reconstruction.
- Factual claims, maps and asset licenses require reviewable evidence metadata; ChatGPT search claims are untrusted until checked.
- UI must expose **Build Prompt**, **Copy/Download Prompt**, **Import JSON**, **Validate**, **Review**, **Approve**, and **Build Repair Prompt** where applicable. Do not display **Generate with AI** as a background action implying a model connection.
- API/source dependency audit fails if production code introduces an LLM SDK, provider API key/env setting, chat completion request, LLM queue, headless ChatGPT automation or hidden internal model call.
- Acceptance includes: prompt builds with no external connection; malformed/duplicate/stale JSON rejected without mutation; approved external JSON can run the deterministic production path offline once media dependencies exist; project resume/review works after restart.

## Scope and migration

R0 is a local, hard-coded 60-second Remotion feasibility experiment and does **not** implement external authoring. In R1, design fresh history-specific contracts and reuse neutral prompt/import validators from the YTB codebase only after isolating them from its old production control plane. The Spine PR #1 contracts and the old one-generated-image-per-beat rules are not product authority for this workflow.

Never replace the manual External ChatGPT handoff with an LLM API for convenience, speed, scheduling or unattended full automation. "From idea to final video" means **all media production after each explicitly imported/approved ChatGPT artifact can be automated**, not that the Studio independently runs ChatGPT.
