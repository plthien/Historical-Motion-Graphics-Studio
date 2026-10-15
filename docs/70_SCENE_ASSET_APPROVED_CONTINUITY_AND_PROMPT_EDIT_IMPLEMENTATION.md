# Scene Asset Approved Continuity + Beat Prompt Edit — Implementation Record

Status: **PASS — phase implementation and independent review complete.**

Release target: `3.0.0-external-authoring.10`

## Baseline and architecture rules

- Baseline package: `3.0.0-external-authoring.9` uploaded candidate containing the first runtime-continuity implementation.
- Compatibility policy: no compatibility readers, migration adapters, backfills, or old-flow fallbacks. Development data is disposable.
- Database policy: no structural DB migration was required; the implementation reuses immutable Artifact/Prompt Bundle/Scene Asset models.
- Ownership: `app.modules.v4` owns Prompt Bundle, Scene Assets, runtime continuity, Scene Asset review, and production generation. Shared media remains execution-only.
- Prompt Bundle remains hidden from Studio navigation.
- Continuity authority: only the **APPROVED image** of the immediately previous beat may satisfy a non-`new_setup` runtime continuity dependency. Current Draft and unapproved candidates are never dependency authority.
- Bulk generation freezes an eligibility snapshot and generates only `new_setup` beats that need generation or continuous beats whose previous dependency is already approved. Newly generated drafts do not unlock later beats inside the same operation.
- Per-beat prompt editing publishes immutable Prompt Bundle revisions with CAS protection. It edits both the main image prompt and the Avoid/negative prompt. Prompt editing does not alter approved Scene Asset continuity authority.

## Major task verdicts

1. Approved-only continuity source authority — **PASS**
2. Backend eligibility and workspace projection — **PASS**
3. Generate One / Section / All eligibility planning — **PASS**
4. Frozen `@prev_beat` lineage and stale-result race protection — **PASS**
5. Approved-source downstream invalidation — **PASS**
6. Approval-gated Scene Assets UI/UX — **PASS**
7. Edit & Save Beat Image Prompt — **PASS**
8. Progress, full regression, independent phase review, release preparation — **PASS**

## Implemented runtime continuity contract

### Approved-only dependency

For `new_setup`, generation is independent and no `@prev_beat` runtime reference is emitted.

For every supported non-`new_setup` continuity relation, Prompt Bundle declares a runtime dependency containing the source beat, relation, and reserved reference tag `prev_beat`. Scene Asset runtime resolves that dependency only from the previous beat's approved selected image. If the previous beat is not generated, not approved, missing, corrupt, or stale, the dependent beat is not generation-eligible.

### Provider reference freeze

Continuous Scene Asset MediaTasks freeze the exact approved source beat, source Artifact ID, SHA-256, relation, and MIME metadata. The provider receives the semantic alias `@prev_beat` / `prev_beat.<verified extension>`. Physical filenames are not semantic authority.

### Stale-result safety

Before a continuity-dependent result can be materialized into the current Scene Asset Manifest, the frozen approved source is compared with the current approved source. A result generated from a superseded approved source cannot enter `candidate_asset_artifact_ids` in the current Manifest.

### Downstream invalidation

Changing Current Draft does not change continuity authority and therefore does not invalidate downstream beats. Replacing or clearing the approved source invalidates only later beats in the same continuity group / until the next `new_setup` boundary. Immutable historical artifacts remain untouched.

## Generation planning

Per-beat Generate is backend-gated. A continuous beat without an approved dependency is rejected even if the frontend is bypassed.

Section and all-remaining operations freeze:

- requested beat IDs;
- eligible beat IDs;
- blocked beats and structured block reasons;
- approved continuity source snapshots for eligible continuous beats.

Only the eligible snapshot is submitted. Newly generated Current Drafts never expand the operation scope.

Job progress uses the frozen eligible scope rather than the count of MediaTasks currently created, preventing early 95% progress during dependency-gated execution.

## Scene Assets UI

The backend workspace exposes canonical continuity readiness and block reasons. The UI presents states such as:

- waiting for previous beat;
- waiting for approval of the previous beat;
- approved continuity ready.

Generate Image is disabled for blocked continuous beats. Generate Section / Generate All remain available when any beats in the requested scope are eligible, while blocked counts remain visible.

Approval actions expose downstream invalidation impact. Current Draft changes do not present continuity invalidation warnings because they are no longer continuity authority.

## Edit & Save Beat Image Prompt

Scene Assets exposes per-beat prompt editing without restoring Prompt Bundle as a Studio stage.

The editor supports one explicit Save operation for:

- Main Image Prompt (`image_prompt`);
- Avoid / Negative Constraints (`negative_prompt`).

Save creates a new immutable approved Prompt Bundle child revision with `prompt_origin = manual_edit`, recomputes `prompt_hash`, and preserves every unrelated prompt item byte-for-byte at the artifact-data level.

Manual prompt save validates the exact canonical reference-tag set. A continuous beat must retain `@prev_beat`; a `new_setup` beat cannot introduce `@prev_beat`; unknown reference tags are rejected.

Existing Scene Asset candidates/approved outputs are not deleted when a beat prompt changes. Candidate freshness is derived from the frozen MediaTask prompt hash versus the current beat prompt hash, so outputs generated from older prompt revisions remain historical/reviewable but are marked as not generated from the current prompt.

Generation and Regeneration use the exact current saved `image_prompt` and `negative_prompt` and freeze their prompt hash/provenance in MediaTask lineage.

`Restore Compiled Prompt` deterministically recompiles only the target beat and publishes another immutable approved Prompt Bundle revision. Full Prompt Bundle recompile remains the explicit canonical reset of all manual prompt edits and intentionally invalidates Scene Assets.

## Reviewer findings caught and fixed

The required Implementer → Reviewer loops found and fixed the following material defects:

1. **P0 stale continuity result binding:** stale candidates were appended before continuity-source validation and could cause Manifest lineage validation failure. Stale-source validation now occurs before current candidate binding.
2. **Progress denominator defect:** progress was calculated from currently-created MediaTasks, allowing early ~95% progress while later work was not in scope yet. Progress now uses the frozen eligible operation scope.
3. **Backend exact-generate eligibility leak:** direct `generate_beat` could generate an already Approved/Candidate beat even though UI disabled it. Exact Generate now only accepts the canonical not-generated state; Regenerate/Retry own their respective states.
4. **Restore Compiled Prompt status defect:** beat recompilation was incorrectly constructed with draft status despite an immediate approved per-beat restore workflow. Restored revisions now satisfy the approved child-revision contract.
5. **Prompt editor completeness gap:** the first prompt-editor implementation persisted only `image_prompt`, while actual provider guidance also contains separate `negative_prompt` / Avoid constraints. The Scene Assets edit contract, backend, frontend, prompt hash, tests, and OpenAPI now save both fields atomically.
6. **Legacy regression assumptions:** older tests expected Current Draft to unlock continuity inside a bulk operation. Tests were updated to the approved-only contract rather than weakening production behavior.

## Regression evidence

Backend / contracts:

- V4 suite excluding the separately chunked unified Scene Asset provider suite and real-media smoke: **173 passed**.
- Unified Scene Asset provider suite: **43 passed**, executed in bounded chunks because one monolithic invocation exceeds the available command timeout.
- Root/platform tests outside V4: **89 passed, 1 skipped**.
- Media reliability + prompt compiler + named-reference focused group: **24 passed**.
- Real FFmpeg/media smoke: **1 passed**.
- Contract validator: **120 schemas, 102 operations, 25 platform registry entries, 86 V4 registry entries validated**.
- OpenAPI parity/closure focused group after prompt-edit schema regeneration: **6 passed**.
- Changed Python modules: `py_compile` **PASS**.

Frontend:

- Scene Assets / hidden Prompt Bundle / polling / scale static source tests: **13 passed**.
- TypeScript transpile syntax check for `SceneAssetsPanel.tsx` and `src/api/client.ts`: **0 errors**.
- Full behavioral React Scene Assets test cannot execute in the supplied package because frontend `node_modules` (including `react`) are not installed. No internet dependency installation was performed. This is recorded as **unable to execute**, not PASS.
- Full `tsc --noEmit` is likewise unavailable without the package's frontend dependencies / `vite/client`; targeted TypeScript transpile is used only as a syntax gate.

## Phase reviewer conclusion

**PASS.**

The approved-only runtime continuity authority is now enforced at backend planning, provider submission, materialization, review invalidation, and UI eligibility boundaries. The implementation no longer uses Current Draft as a continuity fallback, does not auto-chain unapproved generated drafts, and prevents stale approved-source results from becoming current outputs.

Per-beat image-prompt editing is immutable, CAS-protected, reference-safe, generation-traceable, and includes both main and negative prompt material without wiping unrelated Scene Asset outputs.
