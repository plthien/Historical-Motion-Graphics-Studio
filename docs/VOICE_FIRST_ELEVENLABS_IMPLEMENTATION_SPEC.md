# Voice-First ElevenLabs PVC Architecture — 5-Task Implementation Specification

**Repository:** `plthien/ytb-ai-studio`  
**Baseline:** `main` after PR #10 merge  
**Baseline SHA at planning time:** `183fd01e13b2247cc205b4d8ca7369bfc345f8e5`  
**Target branch:** `feat/voice-first-elevenlabs-pvc`  
**Architecture version:** `voice_generation_providers_v1`  
**Status:** historical implementation spec plus current Voice timing invariants. Use `docs/99_SOURCE_OF_TRUTH.md` for active provider scope.

---

## 0. Execution rules

Before every Task, the implementer MUST read:

- `00_START_HERE.md`
- `docs/32_CODING_RULES.md`
- linked source-of-truth / implementation-process docs
- current Voice/Scene/Assembly ADRs
- this entire specification

If implementation HEAD materially conflicts with this spec, report `SPEC_CONFLICT` with exact path/current behavior/required behavior and STOP. Do not silently adapt, add compatibility code, or create a second path.

This is a hard cutover. Production MUST NOT retain parallel old/new timing architectures.

### Active product scope

Voice generation is selected explicitly per operation:

| Provider | Generation identity | Timing |
|---|---|---|---|
| `elevenlabs` | Channel-owned PVC preset `economy -> eleven_flash_v2_5` or `quality_cloned_voice -> eleven_multilingual_v2` | ElevenLabs Forced Alignment |
| `kokoro_local` | explicit voice ID from local Kokoro runtime catalog | ElevenLabs Forced Alignment |

Out of scope:

- Kokoro alignment or timing authority
- Eleven v3
- WhisperX / NeMo / local forced alignment
- PVC training/upload UI
- IVC/stock voices
- provider failover
- automatic repeated paid TTS optimization loops
- generic emotion/style taxonomy
- VFR render redesign

### Authority order

1. Canonical Script = authored text truth.
2. Canonical Spoken Narration = exact intended spoken text after validated overrides.
3. Final paced canonical WAV = physical audio truth.
4. ElevenLabs Forced Alignment on final paced WAV = speech timing truth.
5. Approved Scene Plan = semantic Beat segmentation truth.
6. BeatTimingMap = deterministic visual timing truth.
7. Render frame quantization = final CFR placement only.

Core invariant:

> **Voice does not know Scene. Scene consumes approved Voice timing.**

---

## 1. Frozen provider/internal policies

Use standard TTS endpoint:

`POST /v1/text-to-speech/{voice_id}`

Production request may use:

- `text`
- `model_id`
- `voice_settings.stability`
- `voice_settings.similarity_boost`
- `voice_settings.style`
- `voice_settings.use_speaker_boost`
- `voice_settings.speed`
- `seed`
- `previous_text`
- `next_text`
- optional pronunciation dictionary locators
- `apply_text_normalization`

Do not use `/with-timestamps` as timing authority.

Model request limits:

```text
eleven_flash_v2_5       40,000 chars/request
eleven_multilingual_v2  10,000 chars/request
```

Forced Alignment:

`POST /v1/forced-alignment`

Request:

```text
file = exact final paced section WAV
text = exact canonical spoken section text WITHOUT SSML
```

Treat returned `characters[]` as vendor alignment primitive. Persist loss as telemetry only; V1 has no invented alignment-loss threshold.

Create one policy owner, e.g. `app/modules/v4/voice_first_policy.py`:

```python
VOICE_ARCHITECTURE_VERSION = "voice_generation_providers_v1"
SPOKEN_TEXT_POLICY_VERSION = "spoken_text_overrides_v1"
DELIVERY_POLICY_VERSION = "sparse_pause_cues_v1"
ELEVENLABS_SYNTHESIS_POLICY_VERSION = "elevenlabs_pvc_presets_v1"
FORCED_ALIGNMENT_POLICY_VERSION = "elevenlabs_forced_alignment_v1"
BEAT_TIMING_POLICY_VERSION = "voice_first_beat_timing_v1"

ACTIVE_VOICE_PRESETS = {
    "economy": "eleven_flash_v2_5",
    "quality_cloned_voice": "eleven_multilingual_v2",
}

PAUSE_SECONDS_BY_STRENGTH = {
    "short": 0.25,
    "medium": 0.55,
    "long": 0.90,
}

ELEVENLABS_STYLE = 0.0
DEFAULT_STABILITY = 0.50
DEFAULT_SIMILARITY_BOOST = 0.75
DEFAULT_USE_SPEAKER_BOOST = True
ELEVENLABS_SPEED_MIN = 0.70
ELEVENLABS_SPEED_MAX = 1.20
```

Policy constants are versioned internal choices, not universal provider facts.

---

## 2. Test strategy — minimal but high-value

Do not optimize for test count or coverage percentage.

Add/update tests only when they protect one of these risks:

1. source-of-truth publication/CAS corruption;
2. stale lineage/race conditions;
3. billable provider request duplication;
4. durable restart/fencing correctness;
5. spoken-text mapping errors;
6. visible Beat/audio desynchronization;
7. active legacy/fallback path accidentally surviving.

Prefer service/integration tests over helper-level tests.

Do NOT add tests for:

- trivial Pydantic/ORM field passthrough;
- each enum value independently;
- each error message variation;
- one-line helpers already covered by integration tests;
- duplicate happy paths.

The scenarios explicitly listed under each Task are sufficient unless:

- a real bug is discovered and needs a regression test;
- an existing PR10 regression test must be updated;
- a reviewer identifies a concrete high-risk gap.

If extra tests are added, the PR description must explain why.

---

# Task 1 — Contracts, authoring, DB, Channel PVC foundation

## Goal

Make the new architecture authoritative and establish all contracts/persistence required by runtime Tasks 2–4.

No TTS/Forced Alignment runtime implementation in this Task.

## 1.1 Docs/source-of-truth

Active docs MUST state:

- Voice depends on Script, not Scene Plan.
- Voice and Style/Visual References can proceed in parallel after Script & Analysis.
- Scene Breakdown requires approved Voice.
- only the two ElevenLabs PVC presets are active.
- final paced audio + Forced Alignment is timing authority.
- draft Voice candidate invalidates nothing.
- Voice approval is the invalidation cutover.
- Scene approval owns BeatTimingMap.
- Assembly consumes BeatTimingMap.

Place this spec in repo at:

`docs/VOICE_FIRST_ELEVENLABS_IMPLEMENTATION_SPEC.md`

Do not preserve contradictory active docs as “another supported architecture”.

## 1.2 Extend combined Script & Analysis authoring

Create active V2 output contract:

`contracts/schemas/platform/script_and_analysis_external_output_v2.schema.json`

Each raw Script section MUST include:

```json
{
  "spoken_overrides": [],
  "delivery_cues": []
}
```

Both are required, may be empty.

### `spoken_overrides[]`

```json
{
  "anchor_quote": "D3444",
  "context_quote": "The D3444 was introduced...",
  "spoken_text": "D three four four four"
}
```

Rules:

- exact quote/context from same section narration;
- context + anchor must resolve uniquely;
- plain spoken text only;
- no `<` / `>` / SSML/provider markup;
- resolved override spans cannot overlap.

Backend resolves to global canonical `full_script` offsets and backend-owned IDs:

`spoken override -> sov_<section_id>_<3-digit-order>`

### `delivery_cues[]`

```json
{
  "anchor_quote": "Nobody moved.",
  "context_quote": "Nobody moved. Then the lights went out.",
  "kind": "pause_after",
  "pause_strength": "medium"
}
```

Allowed only:

```text
kind: pause_after
pause_strength: short | medium | long
```

No milliseconds. No emotion/style enums.

Backend-owned ID:

`delivery cue -> dvc_<section_id>_<3-digit-order>`

Cue offset cannot land strictly inside a spoken-override authored span.

### Sensitive-token fence

V1 sensitive characters:

```text
0-9  $  €  £  ¥  %
```

Every occurrence must be inside exactly one spoken override.

If not:

`SCRIPT_SPOKEN_OVERRIDE_REQUIRED`

Backend MUST NOT guess pronunciation.

## 1.3 Channel Voice Profile

Add nullable:

`channels.voice_profile_json`

Null => Voice not ready.

Canonical profile:

```json
{
  "schema_version": 1,
  "provider": "elevenlabs",
  "voice_id": "<PVC voice id>",
  "voice_kind": "professional",
  "default_preset": "quality_cloned_voice",
  "calibration": {
    "economy": {
      "stability": 0.50,
      "similarity_boost": 0.75,
      "use_speaker_boost": true
    },
    "quality_cloned_voice": {
      "stability": 0.50,
      "similarity_boost": 0.75,
      "use_speaker_boost": true
    }
  }
}
```

Channel API owner remains `/api/v3`:

```text
GET /api/v3/channels/{channel_id}/voice-profile
PUT /api/v3/channels/{channel_id}/voice-profile
```

Do not implement PVC training.

## 1.4 Migration `029_voice_first_elevenlabs`

Create:

`alembic/versions/029_voice_first_elevenlabs.py`

```python
revision = "029_voice_first_elevenlabs"
down_revision = "028_durable_voice_workflow"
```

Migration MUST refuse destructive cutover if a Voice operation is active.

Project:

- remove active `narration_timing_map_artifact_id`;
- add `narration_word_timing_map_artifact_id`;
- add `beat_timing_map_artifact_id`;
- keep `narration_audio_artifact_id`.

After cutover those Project Voice pointers mean **approved current Voice only**.

VoiceGenerationOperation:

- remove `scene_plan_artifact_id`;
- remove `expected_narration_timing_map_artifact_id`;
- add `architecture_version`;
- add `expected_narration_word_timing_map_artifact_id`;
- add `voice_preset`.

No new Voice operation may store Scene lineage.

Old Voice working state embeds incompatible lineage. Do not backfill it. Require no active Voice work, purge incompatible working rows/jobs according to FK/runtime rules, clear incompatible downstream pointers, but do not delete media files from disk in Alembic.

Create dedicated `voice_alignment_tasks` table. Do not overload `VoiceSegmentTask`.

Alignment task states:

```text
queued
submitting
completed
failed
submit_outcome_unknown
cancelled
```

Store exact section audio identity, exact spoken transcript/SHA, provider/policy identity, submission/execution fencing, retry state, result/loss/errors.

Do not invent provider async task IDs.

## 1.5 Public Voice request contract

Keep existing `/api/v4/projects/{project_id}/voice...` routes.

Generation request selects preset only.

User must not provide:

- arbitrary provider;
- arbitrary model ID;
- arbitrary voice ID;
- Scene Plan ID.

Resolved PVC comes from Channel Voice Profile.

## Task 1 required tests

Only these new/updated scenarios are required:

1. Combined authoring integration: valid numeric/spoken override + sparse pause resolves deterministically; uncovered sensitive token or ambiguous/overlapping override rejects.
2. Migration/ORM smoke: exact 028→029 works; active Voice blocks migration; new pointers/table exist; Voice operation has no Scene lineage.
3. Channel profile/request integration: one professional PVC profile supports both active presets; arbitrary provider/model/voice selection rejects.

## Task 1 review gate

Run focused tests, self-review, commit, push, create PR, independent review PASS, then STOP. Do not begin Task 2.

---

# Task 2 — ElevenLabs PVC synthesis + spoken delivery + PR10 pacing

## Goal

Produce immutable final paced section WAVs using the explicitly selected generation provider, with no Scene dependency.

Forced Alignment is Task 3.

## 2.1 Active provider/model policy

Production registry exposes both active generation providers:

```text
elevenlabs/economy              -> eleven_flash_v2_5
elevenlabs/quality_cloned_voice -> eleven_multilingual_v2
kokoro_local/<voice_id>         -> local Kokoro runtime catalog voice
```

Model-owned max request sizes:

```text
Flash v2.5        40,000
Multilingual v2   10,000
```

Kokoro Local is selectable for generation only. It is not an alignment provider and is not stored in Channel Voice Profile.

## 2.2 PVC readiness

Before ElevenLabs generation:

- Channel profile exists;
- selected voice is accessible;
- voice is professional/fine-tuned/ready for the selected active model according to actual ElevenLabs response.

Failure:

`VOICE_PVC_NOT_READY`

Before Kokoro generation:

- Kokoro runtime base path and command are configured;
- setup marker/model/voice assets are present;
- selected voice exists in the runtime catalog;
- ElevenLabs alignment credentials are also configured.

## 2.3 Spoken text / delivery compiler

Create one deterministic owner, e.g.:

`app/modules/v4/voice_spoken_text.py`

Per section output:

- canonical spoken text without SSML;
- provider text with sparse break tags;
- authored↔spoken mapping;
- safe authored-boundary map;
- policy hashes.

Example:

```text
Authored:          D3444
Canonical spoken:  D three four four four
```

Delivery cues compile only through policy:

```text
short  -> 0.25s
medium -> 0.55s
long   -> 0.90s
```

Provider text may contain `<break .../>`; canonical spoken text never does.

Do not automatically insert pauses after every sentence.

A future Beat boundary cannot lie strictly inside an authored span expanded by a spoken override.

## 2.4 ElevenLabs synthesis request

Use:

`POST /v1/text-to-speech/{voice_id}`

Freeze in synthesis identity:

- architecture/policy versions;
- preset/model;
- PVC voice ID;
- stability/similarity/speaker boost;
- style=0;
- speed;
- seed;
- canonical spoken hash;
- provider text hash;
- delivery/mapping hash;
- previous/next context hash;
- output format/sample-rate request;
- normalization policy.

Request:

```text
apply_text_normalization = off
```

because YAS3 owns spoken normalization.

Use deterministic best-effort seed derived from immutable synthesis identity. Same retry identity => same seed. Deliberate new variant may change seed.

Do not claim bit-identical reproducibility.

## 2.5 Section-first synthesis

Preferred synthesis unit = one Script section.

Only split if model limit requires it, using deterministic:

```text
paragraph -> sentence -> clause
```

Never split by Beat.

When split, use `previous_text` / `next_text` context and include context in synthesis identity.

## 2.6 Remove Scene dependency from Voice

`VoiceService._load_sources()` loads Script only.

Remove Scene checks from:

- generation readiness;
- operation integrity;
- stale detection;
- reuse;
- pacing remediation lineage.

Scene changes must not invalidate/stale Voice operations.

## 2.7 Preserve PR10 pacing

Keep:

```text
section planned WPM
-> provider speed
-> synthesize
-> measure raw section WPM
-> PR10 quality fence
-> explicit regenerate_section if required
-> small deterministic final correction
-> final paced section audio
```

No automatic paid resynthesis loop.

For explicit section regeneration, ElevenLabs speed recommendation may use measured/target ratio and clamp:

```text
0.70 <= speed <= 1.20
```

Preserve PR10 durable fencing/remediation identity.

## 2.8 Immutable final section audio

Successful pacing creates operation-scoped final section WAV artifacts:

```text
mono
PCM S16LE
48kHz
exact SHA
exact sample count
```

These exact bytes are Task 3 alignment input.

After this point no resample/time-stretch/audio transform is allowed before alignment.

## Task 2 required tests

1. Provider request identity: one Economy + one Quality request proves model, same PVC, settings, style=0, seed, normalization off, continuity, no `/with-timestamps` dependency.
2. Spoken/delivery compiler: authored sensitive token maps correctly; provider text has only intended break; canonical spoken has no SSML; safe boundaries correct.
3. Scene independence: Voice generation/pacing works without Scene Plan and Scene change does not stale it.
4. PR10 regression: out-of-fence pacing terminalizes; explicit regenerate uses bounded recommendation; no automatic paid retry loop.
5. Final-section identity: stored SHA/sample count exactly matches post-pacing bytes prepared for alignment.

## Task 2 review gate

Focused tests + relevant existing PR10 regression suite, self-review, commit/push/PR, independent PASS, STOP.

---

# Task 3 — Final-audio Forced Alignment + draft candidate + approval

## Goal

Make exact final paced audio the measured timing source, create a Scene-free NarrationWordTimingMap, keep generated Voice as draft candidate, and move approved current pointers only on explicit approval.

## 3.1 Durable Forced Alignment

Alignment unit = final paced Script section.

Request:

```text
POST /v1/forced-alignment
file = exact immutable final section WAV
text = exact canonical spoken section text without SSML
```

Never align:

- raw TTS;
- provider-text with break tags;
- Scene/Beat text;
- retimed provider timestamps.

## 3.2 Billable POST safety

Follow PR10 network/durable safety.

- known pre-submit connectivity failure: retryable per durable policy;
- known 429/5xx: retryable per durable policy;
- known auth/validation/credit error: mapped explicitly;
- uncertain outcome after POST may have been accepted: `submit_outcome_unknown`.

For uncertain billable submission:

- persist state;
- do not auto-resubmit;
- surface explicit recovery action;
- do not regenerate TTS;
- do not invent provider idempotency.

## 3.3 Alignment validation

Validate returned characters:

- complete transcript reconstruction;
- finite/non-negative times;
- monotonic start/end;
- within final section duration except tiny documented numeric tolerance.

Persist provider loss as telemetry only.

Convert times deterministically to integer 48kHz samples with one owned rounding policy.

## 3.4 NarrationWordTimingMap

Replace old Scene-coupled timing artifact.

It may contain:

- Script lineage;
- canonical spoken text/mapping;
- Narration Audio lineage;
- section/sample data;
- character timing;
- project-owned word timing;
- provider/policy/loss telemetry.

It MUST NOT contain:

- Scene Plan ID;
- `scenes[]`;
- `beats[]`;
- Beat visual durations;
- weighted fallback.

Vendor word tokenization is not domain authority. Build project-owned words from canonical text/source mapping and character timing.

Full narration audio is exact sample concatenation of final section WAVs. No transform after alignment.

## 3.5 Draft candidate semantics

Successful Voice operation produces operation-owned candidate:

- candidate Narration Audio;
- candidate NarrationWordTimingMap;
- candidate metadata/workspace reference.

It MUST NOT update:

- `project.narration_audio_artifact_id`;
- `project.narration_word_timing_map_artifact_id`.

It MUST NOT invalidate Scene/Visual/Assets/Assembly/Render.

## 3.6 Voice approval CAS

`POST /api/v4/projects/{project_id}/voice/approve`

Approval must atomically:

1. validate exact candidate/current Script/Channel voice lineage;
2. verify candidate complete/not stale;
3. verify audio+WordTiming exact pair;
4. CAS against expected current approved Voice pointers;
5. promote pair as current approved Voice;
6. supersede previous approved pair;
7. invalidate Scene and downstream only after CAS wins;
8. commit once.

Approval does not re-synthesize, re-pace, re-align, copy, or re-encode audio.

## 3.7 Workspace

Voice workspace distinguishes:

- current approved pair;
- latest actionable draft candidate;
- active synthesis/pacing/alignment;
- pacing remediation;
- ambiguous alignment submission.

No Scene/Beat timing projection in Voice workspace.

## Task 3 required tests

1. Alignment authority: exact post-pacing SHA + canonical spoken/no SSML goes to FA; resulting sample map is monotonic and tied to exact audio.
2. Ambiguous billable POST: uncertain outcome persists `submit_outcome_unknown`; no auto second call; no TTS resynthesis.
3. Draft isolation: completed candidate changes no approved Voice/Scene pointers.
4. Approval CAS: exact pair promotes atomically and invalidates downstream; stale race cannot partially publish.
5. Durable fencing: stale alignment completion cannot overwrite a newer execution attempt/candidate.

## Task 3 review gate

Focused tests + durable restart tests relevant to changed code, self-review, commit/push/PR, independent PASS, STOP.

---

# Task 4 — Voice-first Scene + BeatTimingMap + Assembly/UI cutover

## Goal

Move approved Voice upstream of Scene Breakdown, compile deterministic sample-level Beat timing, and make downstream consumers use frozen timing artifacts only.

## 4.1 Scene readiness/reservation

Scene Breakdown requires current approved:

- Script;
- Script Analysis;
- existing Style/Visual prerequisites;
- Narration Audio;
- NarrationWordTimingMap.

Missing/unapproved Voice:

`SCENE_BREAKDOWN_VOICE_NOT_APPROVED`

Scene external-authoring reservation freezes exact approved Voice pair.

If approved Voice changes before import, reject stale.

## 4.2 Timing context for external Scene authoring

Do not dump thousands of raw timestamps.

Backend compiles concise deterministic context:

- section actual durations;
- sentence/clause source spans;
- safe authored boundaries;
- meaningful pause-before durations;
- boundary IDs/source offsets.

LLM still decides semantic Scene/Beat structure and exact Script spans.

LLM does not own final numeric Beat timestamps.


### 4.2A Scene cadence projection after approved Voice

Scene Breakdown remains semantic-first, but post-semantic cadence review uses approved Voice timing rather than Script WPM estimates.

For each Script section:

- backend resolves upstream selected video pacing profile plus frozen Script Analysis density into one `visual_cadence_guidance.preferred_hold_seconds` range;
- Scene LLM receives only that section-local range, not raw `pacing_mode`, raw `video_pacing_profile`, raw `pacing_density`, or `planned_narration_wpm`;
- after candidate semantic beats and same-dominant-still merge-back are stable, Scene uses concise approved-Voice `timing_context` to evaluate realized candidate-beat duration;
- materially long/short candidates trigger semantic rescan only; timing cannot manufacture a Beat boundary;
- backend validation projects accepted Script spans through the exact NarrationWordTimingMap using the same visual-interval policy as BeatTimingMap.

## 4.3 BeatTimingMap

Deterministic compiler consumes approved Scene Plan + approved WordTiming + approved Narration Audio identity.

Per Beat:

```text
source_start
source_end
speech_start_sample
speech_end_sample
visual_start_sample
visual_end_sample
```

Reject Beat boundary strictly inside an expanded spoken-override authored span.

### Fixed visual ownership policy

```text
visual_start[first] = 0
visual_start[i] = speech_start[i] for i > first
visual_end[i] = visual_start[i+1] for i < last
visual_end[last] = total_narration_sample_count
```

Meaning:

- leading silence belongs first image;
- pause between Beat A speech and Beat B speech remains on Beat A image;
- trailing silence belongs last image;
- no visual gaps/overlaps.

Hard product requirement:

> Beat B image must not appear while Beat A narration is still speaking, and Beat A image must not remain after Beat B speech begins, except final CFR quantization bounded by render policy.

## 4.4 Atomic Scene approval

Extend current atomic Scene publication to exactly four artifacts:

- approved Scene Plan;
- approved Visual Plan;
- approved Prompt Bundle;
- approved BeatTimingMap.

One Project CAS updates all four.

Commit guard freezes approved Voice pair.

No current approved new-architecture Scene may exist with missing/stale BeatTimingMap.

## 4.5 Assembly/subtitles

Assembly requires current approved:

- Script;
- Scene Plan;
- Scene Asset Manifest;
- Narration Audio;
- NarrationWordTimingMap;
- BeatTimingMap.

Visual timing comes only from BeatTimingMap.

Remove Assembly logic that derives Beat boundaries from Voice `timing["beats"]`.

`estimated_duration_seconds` remains planning/QC only.

Subtitles use NarrationWordTimingMap directly.

Render frame quantization remains final CFR rounding layer; narration is never stretched to planned Scene durations.

## 4.6 Invalidation graph

Exact behavior:

```text
Script change
  -> Voice stale/invalid
  -> existing Style/Scene/downstream invalidation as appropriate

Style/Visual change
  -> Scene/downstream
  -> NOT Voice

Voice DRAFT completion
  -> NOTHING downstream

Voice APPROVAL change
  -> Scene Plan
  -> Visual Plan
  -> Prompt Bundle
  -> BeatTimingMap
  -> Scene Assets
  -> Assembly/Subtitles
  -> Render

Scene change
  -> Scene downstream
  -> NOT Voice

Scene Asset Manifest change
  -> Assembly/Render
```

Use approval-specific invalidation owner, e.g. `invalidate_from_voice_approval`.

Do not invoke it from candidate finalization.

## 4.7 Frontend flow

Visible order:

```text
Topic
Script & Analysis
Style Bible / Visual References
Voice
Scene Breakdown
Scene Assets
Assembly
Render
```

Voice and Style/Visual may run in parallel after Script & Analysis.

Scene unlock requires approved Voice plus existing Style/Visual prerequisites.

Voice UI shows:

- PVC readiness;
- exactly two presets;
- Kokoro Local generation readiness and runtime voice catalog;
- synthesis/pacing/alignment progress;
- draft candidate review;
- approved current Voice;
- explicit approve;
- pacing remediation;
- ambiguous alignment recovery.

Do not show Kokoro as an alignment provider, Eleven v3, WhisperX/NeMo/local forced alignment, or arbitrary ElevenLabs model IDs.

## Task 4 required tests

1. Scene readiness/staleness: no approved Voice blocks Scene prompt; Voice approval change stales reservation.
2. Beat timing correctness: one pause timeline proves sample 0 start, hold-through-pause, cut at next speech start, exact final sample, no gap/overlap.
3. Atomic Scene CAS: four artifacts publish together; Voice race/stage failure cannot partially promote.
4. Assembly authority: BeatTimingMap drives visual timeline, WordTiming drives subtitles, total equals Voice samples, no planned-duration/legacy fallback.
5. Workflow integration: draft Voice leaves Scene usable; approved Voice invalidates Scene/downstream; Scene change leaves Voice valid; frontend gating matches backend.

## Task 4 review gate

Focused integration tests + relevant Assembly/render regression, self-review, commit/push/PR, independent PASS, STOP.

---

# Task 5 — Legacy removal + exact-head validation + real-provider acceptance

## Goal

Remove obsolete active paths, prove architecture closure, and validate both presets on exact candidate HEAD.

Task 5 should add almost no new feature behavior.

## 5.1 Remove active legacy paths

Remove active references to:

- `narration_timing_map_artifact_id`;
- old active NarrationTimingMap model/schema;
- Scene lineage inside Voice artifacts/operations;
- Voice workspace `scenes[]`/`beats[]`;
- production `weighted_text_alignment_v1` fallback;
- provider timestamps as canonical timing;
- `/with-timestamps` production requirement;
- Voice Scene readiness;
- Assembly legacy timing derivation;
- Kokoro as an alignment/timing provider or Channel Voice Profile entry.

Kokoro files may remain as the local generation runtime and CLI tooling.

Do not add a legacy runtime switch.

## 5.2 Static search closure

At candidate HEAD classify every hit:

```text
narration_timing_map_artifact_id
weighted_text_alignment_v1
with-timestamps
VOICE_NOT_READY
kokoro
source_scene_plan_revision_id
```

`source_scene_plan_revision_id` may remain in Scene-owned domains; it must not remain as Voice lineage.

No unexplained active production hit is acceptable.

## 5.3 Contract/build closure

Update/regenerate repo-required:

- OpenAPI parity/snapshots;
- schema manifests/indexes;
- frontend generated/shared types;
- active architecture docs;
- static legacy-removal assertions.

No parallel V1/V2 authoring/timing runtime remains.

## 5.4 Exact-head gates

Run repo-required gates, at minimum:

- Alembic upgrade/head checks;
- focused Voice/Scene/Assembly tests;
- full backend suite;
- external authoring suite;
- OpenAPI/schema parity;
- frontend typecheck/tests/build;
- static removal checks.

Do not use PR10 historical green as evidence.

## 5.5 Real ElevenLabs acceptance

Use real Creator-or-higher account, accessible fine-tuned PVC, enough credits, no secrets in committed artifacts.

Run both presets end-to-end:

```text
Script
-> ElevenLabs PVC synthesis
-> pacing
-> immutable final section audio
-> Forced Alignment
-> draft candidate
-> approve Voice
-> Scene Breakdown
-> Scene approval + BeatTimingMap
-> Assembly
```

Run once with Flash v2.5 and once with Multilingual v2.

## 5.6 Manual quality/timing acceptance

Use at least one realistic ~10-minute narration.

Check Voice quality:

- narrator identity stable;
- no obvious section/chunk join discontinuity;
- sparse explicit pauses sound intentional;
- normalization-sensitive tokens match spoken overrides;
- deterministic final pacing correction does not audibly damage delivery.

Check at least ~30 meaningful Beat-candidate onsets including pauses, section transitions, short lines, and spoken-override vicinity.

Reject if reproducible:

- Beat B image starts while Beat A narration is still speaking;
- Beat A image remains after Beat B speech begins;

except bounded final CFR quantization.

Do not invent numeric alignment-loss acceptance thresholds without evidence.

## 5.7 Failure-path acceptance

Exercise/simulate only high-risk paths:

- TTS 429/credits/provider failure;
- ambiguous TTS behavior already protected by PR10;
- ambiguous Forced Alignment submit;
- restart while aligning;
- stale alignment completion;
- Voice approval CAS race;
- Voice changes after Scene reservation;
- stale BeatTimingMap rejected by Assembly.

## Task 5 required new automated tests

Only two new scenarios are required unless bugs are discovered:

1. Static architecture closure: Kokoro is generation-only, not timing authority; no weighted/legacy timing path.
2. One full fake-provider integration smoke: Script→Voice candidate→approve→Scene→BeatTiming→Assembly with exact lineage/timing authority.

Everything else should reuse/update existing regression suites plus real-provider/manual acceptance.

## Task 5 review gate

Run all exact-head gates and real-provider acceptance, self-review, commit/push/PR, independent PASS. Only then is the architecture cutover done.

---

## Shared public API expectations

Keep Voice routes in existing `/api/v4` owner:

```text
GET  /api/v4/projects/{project_id}/voice
POST /api/v4/projects/{project_id}/voice/jobs
GET  /api/v4/projects/{project_id}/voice/jobs/{job_id}
POST /api/v4/projects/{project_id}/voice/jobs/{job_id}/cancel
POST /api/v4/projects/{project_id}/voice/sections/{section_id}/regenerate/jobs
POST /api/v4/projects/{project_id}/voice/approve
GET  /api/v4/projects/{project_id}/voice/audio
GET  /api/v4/projects/{project_id}/voice/sections/{section_id}/audio
GET  /api/v4/projects/{project_id}/voice/segments/{segment_id}/audio
```

Do not add parallel V5 Voice routes.

Channel profile routes live in `/api/v3` Platform owner.

---

## Shared Voice state/error expectations

Preserve coarse durable operation status vocabulary where possible; do not migrate statuses just for naming aesthetics.

Logical phase:

```text
queued
-> synthesis
-> pacing
-> final section audio ready
-> alignment
-> candidate finalization
-> completed
```

Terminal outcomes:

```text
completed
failed
superseded
cancelled
```

Pacing failure remains:

`VOICE_PACING_REGENERATION_REQUIRED`

New error names, unless exact semantic equivalent already exists:

```text
VOICE_CHANNEL_PROFILE_REQUIRED
VOICE_PVC_NOT_READY
VOICE_PRESET_UNSUPPORTED
SCRIPT_SPOKEN_OVERRIDE_REQUIRED
SCRIPT_SPOKEN_OVERRIDE_INVALID
SCRIPT_SPOKEN_OVERRIDE_OVERLAP
SCRIPT_DELIVERY_CUE_BOUNDARY_INVALID
VOICE_SPOKEN_TEXT_INVALID
VOICE_AUTHORED_BOUNDARY_UNSAFE
VOICE_ALIGNMENT_NOT_READY
VOICE_ALIGNMENT_RESPONSE_INVALID
VOICE_ALIGNMENT_TEXT_MISMATCH
VOICE_ALIGNMENT_OUT_OF_RANGE
VOICE_ALIGNMENT_SUBMIT_OUTCOME_UNKNOWN
VOICE_ALIGNMENT_FAILED
VOICE_CANDIDATE_NOT_FOUND
VOICE_CANDIDATE_STALE
VOICE_APPROVAL_CONFLICT
SCENE_BREAKDOWN_VOICE_NOT_APPROVED
SCENE_BREAKDOWN_VOICE_STALE
BEAT_TIMING_MAP_INVALID
BEAT_TIMING_LINEAGE_STALE
BEAT_TIMING_BOUNDARY_UNSAFE
```

Do not create synonym error codes for an existing exact equivalent.

---

## Artifact ownership after cutover

| Artifact | Owner | Current pointer means |
|---|---|---|
| Script | Platform authoring | current approved source |
| Script Analysis | Platform authoring | current approved source |
| Narration Audio | Voice | approved Voice only |
| NarrationWordTimingMap | Voice | approved Voice only |
| Scene Plan | Scene Breakdown | approved/current |
| Visual Plan | Scene Breakdown | approved/current |
| Prompt Bundle | Scene approval compiler | approved/current |
| BeatTimingMap | Scene approval compiler | approved/current Scene + Voice lineage |
| Scene Asset Manifest | Scene Assets | approved/current |
| Assembly Plan | Assembly | approved/current |

Draft Voice artifacts are operation-owned and not Project current pointers.

---

## Non-negotiable invariants

These are the primary correctness targets:

1. Voice generation succeeds without Scene Plan.
2. Scene cannot begin without approved Voice.
3. Draft Voice completion changes no approved Voice pointer.
4. Draft Voice completion invalidates nothing downstream.
5. Voice approval is atomic CAS publication.
6. Voice approval invalidates Scene/downstream.
7. Scene change never invalidates Voice.
8. Forced Alignment runs only after final pacing.
9. No audio transform occurs after alignment except exact section concatenation.
10. Alignment transcript has no SSML.
11. NarrationWordTimingMap contains no Scene/Beat timing.
12. BeatTimingMap carries exact approved Scene + Voice lineage.
13. Visual timeline is contiguous sample 0 → final narration sample.
14. Beat image transition occurs at next Beat speech start.
15. Assembly consumes BeatTimingMap; no WPM timing recomputation.
16. Subtitles consume NarrationWordTimingMap.
17. Production timing has no weighted fallback.
18. Production Kokoro is generation-only and never alignment/timing authority.
19. User cannot select arbitrary ElevenLabs model IDs.
20. Same Channel PVC can serve both presets.

When deciding whether to add a test, identify which invariant it protects. If none, the test is probably unnecessary.

---

## Expected high-impact files

Directional list; if ownership materially moved at implementation HEAD, use `SPEC_CONFLICT`.

Platform/contracts:

- `app/modules/platform/external_script_analysis.py`
- combined Script & Analysis prompt/input builders
- Script V2 schemas/read contracts

DB/repositories:

- `app/db/models.py`
- `app/repositories/core.py`
- Voice durable repositories
- `alembic/versions/029_voice_first_elevenlabs.py`

Provider/Voice:

- `app/modules/v4/elevenlabs_client.py`
- `app/modules/v4/elevenlabs_model_policy.py`
- `app/modules/v4/elevenlabs_provider_adapter.py`
- active TTS registry/settings
- `app/modules/v4/voice_service.py`
- `app/modules/v4/durable_voice_service.py`
- durable Voice coordinator/workflow/finalizer/materializer
- `app/modules/v4/voice_pacing.py`
- `app/modules/v4/voice_segmentation.py`
- new spoken-text compiler
- new alignment task/service owner
- new NarrationWordTimingMap owner
- Voice API schemas/routes

Scene/downstream:

- Scene external authoring reservation/input/validation
- Scene approval CAS/guards
- new BeatTimingMap compiler
- `app/modules/v4/assembly_service.py`
- subtitle compiler
- invalidation owner
- Studio gating / Voice panel / API client types

---

## Five-task PR/handoff rule

For each Task:

```text
Task:
Baseline SHA:
Candidate SHA:
Files changed:
Contracts changed:
DB impact:
Runtime ownership changed:
Old paths removed:
Focused required scenarios run:
Results:
Full gates run (if applicable):
Real-provider behavior tested:
SPEC_CONFLICTS: none | list
PR number / URL:
Reviewer result: PASS | FAIL
```

Implementation sequence:

```text
Task 1 -> PR/review PASS
Task 2 -> PR/review PASS
Task 3 -> PR/review PASS
Task 4 -> PR/review PASS
Task 5 -> PR/review PASS
```

Do not start the next Task before independent PASS.
