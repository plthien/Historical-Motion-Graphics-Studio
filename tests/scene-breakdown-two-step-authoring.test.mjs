import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const sceneSchema = readFileSync(new URL('../contracts/schemas/v4/scene_breakdown_scene_llm_output_v2.schema.json', import.meta.url), 'utf8');
const sceneInputSchema = readFileSync(new URL('../contracts/schemas/v4/scene_breakdown_scene_authoring_input_v2.schema.json', import.meta.url), 'utf8');
const visualSchema = readFileSync(new URL('../contracts/schemas/v4/scene_breakdown_visual_llm_output_v2.schema.json', import.meta.url), 'utf8');
const scenePrompt = readFileSync(new URL('../contracts/prompts/v4/scene_breakdown_scene/system.md', import.meta.url), 'utf8');
const sceneUserPrompt = readFileSync(new URL('../contracts/prompts/v4/scene_breakdown_scene/user.md', import.meta.url), 'utf8');
const visualPrompt = readFileSync(new URL('../contracts/prompts/v4/scene_breakdown_visual/system.md', import.meta.url), 'utf8');
const visualUserPrompt = readFileSync(new URL('../contracts/prompts/v4/scene_breakdown_visual/user.md', import.meta.url), 'utf8');
const semanticValidator = readFileSync(new URL('../app/modules/v4/scene_breakdown_semantic_validator.py', import.meta.url), 'utf8');
const service = readFileSync(new URL('../app/modules/v4/scene_breakdown_two_step_external_service.py', import.meta.url), 'utf8');
const reservations = readFileSync(new URL('../app/services/external_authoring_reservations.py', import.meta.url), 'utf8');
const routes = readFileSync(new URL('../app/api/routes/scene_breakdown_two_step.py', import.meta.url), 'utf8');
const main = readFileSync(new URL('../app/main.py', import.meta.url), 'utf8');
const panel = readFileSync(new URL('../src/features/studio/SceneBreakdownPanel.tsx', import.meta.url), 'utf8');
const studio = readFileSync(new URL('../src/pages/StudioPage.tsx', import.meta.url), 'utf8');
const docs = readFileSync(new URL('../docs/67_SCENE_BREAKDOWN_VISUAL_MERGE_COMPLIANCE.md', import.meta.url), 'utf8');

test('Step 1 schema owns semantic state and excludes final visual design', () => {
  for (const field of ['meaning_now', 'visible_state_now', 'delta_now', 'continuity_with_previous']) {
    assert.match(sceneSchema, new RegExp(`"${field}"`));
  }
  assert.doesNotMatch(sceneSchema, /"visual_concept"|"subject_action"|"environment_emphasis"|"composition"/);
});

test('Step 1 input represents Scene source once with the complete legal boundary lattice', () => {
  assert.match(sceneInputSchema, /"source_segments"/);
  assert.match(sceneInputSchema, /"boundary_id_after"/);
  assert.match(sceneInputSchema, /"preferred_hold_seconds"/);
  assert.doesNotMatch(sceneInputSchema, /"legal_boundaries"|"time_from_scene_start_seconds"|"pause_after_seconds"/);
  assert.doesNotMatch(sceneInputSchema, /"duration_seconds"|"hold_seconds"|"out_of_band_count"|"timing_context"|"timing_atoms"/);
});

test('Step 1 runtime uses the V9 prompt and V2 input contract only', () => {
  assert.match(service, /scene_breakdown_scene_external_v9/);
  assert.match(service, /scene_breakdown_scene_authoring_input_v2\.schema\.json/);
  assert.doesNotMatch(service, /scene_breakdown_scene_external_v8/);
  assert.doesNotMatch(service, /scene_breakdown_scene_authoring_input_v1\.schema\.json/);
});

test('Step 2 schema maps exact scene and beat IDs and does not author continuity', () => {
  assert.match(visualSchema, /"scene_id"/);
  assert.match(visualSchema, /"beat_id"/);
  assert.match(visualSchema, /"visual_concept"/);
  assert.match(visualSchema, /"subject_action"/);
  assert.match(visualSchema, /"environment_emphasis"/);
  assert.match(visualSchema, /"composition"/);
  assert.doesNotMatch(visualSchema, /"continuity_with_previous"/);
});

test('Step 2 schema and prompt lock representation families and plural location refs', () => {
  const visual = JSON.parse(visualSchema);
  const visualProperties = visual.properties.scenes.items.properties.beat_plans.items.properties;
  assert.deepEqual(visualProperties.representation_family.enum, [
    'character_environment_lived',
    'object_evidence_closeup',
    'text_diagram_editorial',
  ]);
  assert.equal(visualProperties.selected_location_refs.type, 'array');
  assert.equal(visualProperties.selected_location_refs.items.pattern, '^loc_[0-9]{3}$');
  assert.match(visualPrompt, /semantic keyframes selected by Step 1/);
  assert.match(visualPrompt, /54% Character \/ Environment \/ Lived Scene/);
  assert.match(visualPrompt, /13% Object \/ Evidence Close-up/);
  assert.match(visualPrompt, /33% Text \/ Diagram \/ Editorial/);
  assert.match(visualPrompt, /complete Visual Plan/);
  assert.match(visualPrompt, /every genuine locked `delta_now`, regardless of magnitude/);
  assert.match(visualPrompt, /single frozen keyframe/);
  assert.doesNotMatch(visualPrompt, /cadence[- ](?:sized|validated)/i);
});

test('Step prompts preserve prompt-WHAT / skill-HOW authority and separate semantic from visual ownership', () => {
  for (const prompt of [scenePrompt, visualPrompt]) {
    assert.match(prompt, /prompt owns the required outcome, hard acceptance constraints, and output contract/i);
    assert.match(prompt, /current authoring skill.*owns HOW/i);
    assert.match(prompt, /prompt and schema are authoritative/i);
  }
  assert.match(scenePrompt, /Step 1 stops at the locked semantic Scene\/Beat plan/);
  assert.match(scenePrompt, /all source after each Beat is hidden/i);
  assert.doesNotMatch(scenePrompt, /reference_context/);
  assert.doesNotMatch(scenePrompt, /epistemic status/i);
  assert.match(visualPrompt, /locked Scene Plan is semantic authority/i);
  assert.match(visualPrompt, /script_quote.*orientation context only/is);
  assert.doesNotMatch(visualPrompt, /scene_purpose.*narrative_role.*orientation context only/is);
  assert.match(visualPrompt, /environment_emphasis.*environment NOW/is);
  assert.match(visualPrompt, /reusable prose\/layout shell with noun substitution/i);
  assert.match(visualPrompt, /editorial graphic/i);
  assert.match(visualPrompt, /visible_text.*semantically entailed/is);
  assert.match(visualPrompt, /one-glance test/i);
  assert.match(visualPrompt, /editorial_language.*supplied editorial visual grammar/is);
  assert.match(visualPrompt, /not asset types or schema discriminators/i);
  assert.match(visualPrompt, /`canonical` means a supplied recurring identity, location, prop, subject, or motif/i);
  assert.match(visualPrompt, /Use it only to decide whether a supplied reference may or must be selected/i);
  assert.match(visualPrompt, /For motifs, use `label` and `description` to identify the recurring visual idea/i);
  assert.match(visualPrompt, /`visual_traits` to preserve its supplied stable visible characteristics/i);
  assert.match(visualPrompt, /`continuity_notes` to preserve supplied stability\/change rules/i);
  assert.match(visualPrompt, /`evidence_script_texts` only as direct supplied anchors for motif eligibility/i);
  assert.match(visualPrompt, /Motif descriptors do not authorize a new story fact or character\/location\/prop\/subject presence/i);
  assert.match(visualPrompt, /motif eligibility does not force selection/i);
  assert.match(scenePrompt, /independently drawable explanatory\/editorial facet/i);
});

test('long-context prompt endings repeat only the highest-value acceptance invariants', () => {
  assert.match(sceneUserPrompt, /# Final acceptance reminder/);
  assert.match(sceneUserPrompt, /hiding all source after each Beat/);
  assert.match(sceneUserPrompt, /timing did not manufacture semantic material or a boundary with no independent source-owned contribution/);
  assert.match(visualUserPrompt, /# Final acceptance reminder/);
  assert.match(visualUserPrompt, /no visual entry uses information from a later Beat/);
  assert.match(visualUserPrompt, /reference selection is bidirectional/);
  assert.match(visualUserPrompt, /lexical variety is not being used to disguise repeated generic visual templates/);
});

test('Visual prompt enforces one-frozen-image-first authoring and post-hoc family classification', () => {
  assert.match(visualPrompt, /one concrete frozen image/i);
  assert.match(visualPrompt, /All visual fields must describe different aspects of that same single image/i);
  assert.match(visualPrompt, /Do not generate the fields independently as separate semantic solutions/i);
  assert.match(visualPrompt, /Do not choose `representation_family` before the concrete still has been authored/i);
  assert.match(visualPrompt, /never be used as a template selector/i);
  assert.match(visualPrompt, /Do not pre-allocate Beat counts to representation families/i);
  assert.match(visualPrompt, /post-hoc whole-plan audit/i);
  assert.match(visualPrompt, /never alter a stronger visual solely to move a percentage/i);
  assert.match(visualPrompt, /family factories/i);
  assert.match(visualPrompt, /same physical\/editorial frozen image at the same instant/i);
  assert.match(visualPrompt, /conceive the still -> identify which canonical identities are actually visible -> select refs/i);
  assert.match(visualPrompt, /mismatch invalidates the classification, not automatically the visual/i);
  assert.match(visualPrompt, /correct the classification first/i);
  assert.match(visualPrompt, /do not alter a semantically valid still merely to fit the existing family label/i);
  assert.match(visualPrompt, /PASS B - Frozen-image semantic integrity/i);
  assert.match(visualPrompt, /PASS G - Whole-plan 54 \/ 13 \/ 33 calibration/i);
  assert.doesNotMatch(visualPrompt, /Author each final field independently from the same locked current Beat state/);
  assert.match(visualPrompt, /Do not create field separation cosmetically/);
  assert.match(visualPrompt, /minimum concrete prose sufficient to direct the still/);
  assert.match(visualPrompt, /Good field separation:/);
  assert.match(visualPrompt, /The principle is field-role separation plus locked-state ownership/);
});

test('Visual prompt separates hard semantic failures from diagnostic warnings', () => {
  assert.match(visualPrompt, /Hard failures versus diagnostic warnings/);
  assert.match(visualPrompt, /fields cannot coexist in one frozen image/);
  assert.match(visualPrompt, /visible supported canonical identity is missing its required ref/);
  assert.match(visualPrompt, /diagnostic warnings only/i);
  assert.match(visualPrompt, /Do not optimize prose merely to evade a diagnostic warning/i);
  assert.match(visualPrompt, /wrapper failure/);
  assert.match(visualPrompt, /same-image contradiction/);
  assert.match(visualPrompt, /generic template leakage/);
  assert.match(visualPrompt, /reference bidirectionality/);
});

test('Step 1 input is context-pruned while Step 2 receives only locked Beat state plus visual authority', () => {
  assert.match(service, /def _scene_prompt_input/);
  assert.match(service, /"artifact_id": artifact_id/);
  assert.match(service, /project_scene_authoring_input/);
  assert.match(service, /_project_authoring_plan/);
  assert.doesNotMatch(service, /def _semantic_reference_context/);
  assert.doesNotMatch(service, /"narration_context": deepcopy/);
  assert.doesNotMatch(service, /"reference_context": cls\._semantic_reference_context/);
  assert.match(service, /separators=\(",", ":"\)/);
  assert.doesNotMatch(service.slice(service.indexOf('def _locked_visual_input'), service.indexOf('def _build_visual_package')), /"narrative_role"|"scene_purpose"|"estimated_duration_seconds"/);
  assert.match(service, /"meaning_now": locked_beat\["meaning_now"\]/);
  assert.match(service, /"visible_state_now": locked_beat\["visible_state_now"\]/);
  assert.match(service, /"delta_now": locked_beat\["delta_now"\]/);
  assert.match(service, /"visual_context": deepcopy\(visual_context\)/);
});

test('semantic validation rejects definite timing-container collapse and reuses deterministic source/timing validation', () => {
  assert.match(semanticValidator, /validate_and_normalize_section/);
  assert.match(semanticValidator, /Semantic authoring placeholder; not persisted/);
  assert.match(semanticValidator, /duplicate_locked_beat_state/);
  assert.match(semanticValidator, /SCENE_BREAKDOWN_SEMANTIC_STATE_COLLAPSE/);
  assert.match(semanticValidator, /beat\.pop\("visual", None\)/);
  assert.match(semanticValidator, /visual\["continuity_with_previous"\] = source_beat\["continuity_with_previous"\]/);
});

test('two-step service converts legacy validator exceptions to stable 422 AppErrors', () => {
  assert.match(service, /SceneBreakdownValidationError/);
  assert.match(service, /def _validation_error/);
  assert.match(service, /SCENE_BREAKDOWN_SCENE_VALIDATION_FAILED/);
  assert.match(service, /SCENE_BREAKDOWN_VISUAL_VALIDATION_FAILED/);
});

test('Step 1 creates a pending Visual reservation but never publishes the project pair', () => {
  const start = service.indexOf('def import_scene_result');
  const end = service.indexOf('def _validate_visual_import');
  const body = service.slice(start, end);
  assert.match(body, /VISUAL_PLAN_STAGE/);
  assert.match(body, /scene_plan_artifact_id/);
  assert.match(body, /locked_fragments/);
  assert.match(body, /visual_prompt_package/);
  assert.doesNotMatch(body, /publish_scene_visual_plan_with_completed_run/);
});

test('rebuilding Step 1 invalidates pending Visual and stale legacy combined reservations', () => {
  assert.match(reservations, /if stage == SCENE_PLAN_STAGE:/);
  assert.match(reservations, /stages_to_clear\.extend\(\[VISUAL_PLAN_STAGE, SCENE_STAGE\]\)/);
  assert.match(reservations, /ExternalAuthoringReservation\.stage\.in_\(stages_to_clear\)/);
});

test('Step 2 verifies exact identity/order, field independence and current-Beat reference eligibility', () => {
  const validationStart = service.indexOf('def _validate_visual_import');
  const publishStart = service.indexOf('def import_visual_result');
  const visualBody = service.slice(validationStart);
  assert.match(visualBody, /actual_scene_ids != expected_scene_ids/);
  assert.match(visualBody, /actual_beats != expected_beats/);
  assert.match(service, /visual_field_collapse/);
  assert.match(service, /visual_reference_outside_locked_eligibility/);
  assert.match(visualBody, /publish_scene_visual_plan_with_completed_run/);
  assert.equal((service.match(/publish_scene_visual_plan_with_completed_run/g) ?? []).length, 1);
  assert.ok(publishStart > validationStart);
});

test('runtime authoring cutover disables every legacy combined Scene Breakdown authoring route', () => {
  assert.match(main, /_LEGACY_COMBINED_SCENE_AUTHORING_ROUTES/);
  assert.match(main, /get_pending_scene_breakdown_prompt_v4/);
  assert.match(main, /build_scene_breakdown_prompt_v4/);
  assert.match(main, /validate_scene_breakdown_import_v4/);
  assert.match(main, /import_scene_breakdown_v4/);
  assert.match(main, /_disable_legacy_combined_scene_authoring\(\)/);
});

test('Scene Breakdown UI is bound to the selected project prop and has no localStorage project lookup', () => {
  assert.match(panel, /projectId: string/);
  assert.match(panel, /const projectId = props\.projectId/);
  assert.doesNotMatch(panel, /SELECTED_PROJECT_STORAGE_KEY|readStoredSelection/);
  assert.match(studio, /<SceneBreakdownPanel projectId=\{projectId\}/);
  assert.doesNotMatch(studio, /buildSceneBreakdownPromptV4|importSceneBreakdownV4|getPendingSceneBreakdownPromptV4/);
});

test('routes expose two internal authoring steps under Scene Breakdown only', () => {
  assert.match(routes, /scene-breakdown\/scene\/prompt/);
  assert.match(routes, /scene-breakdown\/scene\/import/);
  assert.match(routes, /scene-breakdown\/visual\/prompt/);
  assert.match(routes, /scene-breakdown\/visual\/import/);
  assert.doesNotMatch(routes, /\/visual-planner/);
});

test('owner doc locks half-pair prevention and one-screen two-step behavior', () => {
  assert.match(docs, /one top-level `Scene Breakdown` Studio stage/);
  assert.match(docs, /Step 1 does \*\*not\*\* change `project\.scene_plan_artifact_id`/);
  assert.match(docs, /Project pointers must never expose a half-pair state/);
  assert.match(docs, /Step-2 scene\/Beat identity and order must match Step 1 exactly/);
});
