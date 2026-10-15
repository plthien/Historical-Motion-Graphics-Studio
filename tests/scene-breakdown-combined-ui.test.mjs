import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const panel = readFileSync(new URL('../src/features/studio/SceneBreakdownPanel.tsx', import.meta.url), 'utf8');
const studio = readFileSync(new URL('../src/pages/StudioPage.tsx', import.meta.url), 'utf8');
const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8');
const twoStepClient = readFileSync(new URL('../src/api/sceneBreakdownTwoStep.ts', import.meta.url), 'utf8');
const progress = readFileSync(new URL('../src/features/projects/projectProgress.ts', import.meta.url), 'utf8');
const stageTypes = readFileSync(new URL('../src/features/studio/types.ts', import.meta.url), 'utf8');

test('Scene Breakdown renders section selector, scene cards, drawer, and beat visual cards', () => {
  assert.match(panel, /scene-breakdown-section-selector/);
  assert.match(panel, /scene-breakdown-card-grid/);
  assert.match(panel, /scene-breakdown-scene-card/);
  assert.match(panel, /scene-breakdown-drawer/);
  assert.match(panel, /scene-breakdown-beat-card/);
  assert.match(panel, /Open Scene/);
  assert.match(panel, /VisualDetails/);
  assert.match(panel, /visual_concept/);
  assert.match(panel, /visible_text/);
  assert.doesNotMatch(panel, /asset_strategy|asset_details|AssetDetails/);
  assert.match(panel, /continuity_with_previous/);
});

test('Scene Breakdown authoring is split into Scene/Beat then Visual inside the same panel', () => {
  assert.match(panel, /scene-breakdown-authoring-steps/);
  assert.match(panel, /Step 1/);
  assert.match(panel, /Scene & Beat Plan/);
  assert.match(panel, /Step 2/);
  assert.match(panel, /Visual Plan/);
  assert.match(panel, /Complete Step 1 first/);
  assert.match(panel, /semantic Scene\/Beat states first/);
  assert.match(twoStepClient, /scene-breakdown\/scene\/prompt/);
  assert.match(twoStepClient, /scene-breakdown\/scene\/import/);
  assert.match(twoStepClient, /scene-breakdown\/visual\/prompt/);
  assert.match(twoStepClient, /scene-breakdown\/visual\/import/);
});

test('Scene Breakdown editor still reconstructs and validates the published semantic plus visual pair', () => {
  assert.match(panel, /editableSection\(scenePlan, visualPlan, sectionId\)/);
  assert.match(panel, /visualForEdit/);
  assert.match(panel, /validatedText/);
  assert.match(panel, /validationArtifactId/);
  assert.match(panel, /Format JSON/);
  assert.match(panel, /Validate/);
  assert.match(panel, /Save Section/);
  assert.match(panel, /Approve & Build Prompts/);
  assert.match(panel, /Image aspect ratio/);
  assert.match(studio, /aspect_ratio: imageAspectRatio/);
});

test('Studio keeps one Scene Breakdown top-level stage and no independent Visual Planner stage', () => {
  assert.match(studio, /<SceneBreakdownPanel/);
  assert.match(studio, /scenePlan=\{scenePlan\}/);
  assert.match(studio, /visualPlan=\{visualPlan\}/);
  assert.doesNotMatch(studio, /VisualPlannerPanel/);
  assert.doesNotMatch(stageTypes, /'visual'/);
  assert.doesNotMatch(client, /generateVisualPlanV4|regenerateVisualPlan|approveVisualPlan|getVisualPlannerJob/);
  assert.match(client, /getVisualPlanV4/);
});

test('project progress treats Scene Plan and Visual Plan as one paired Scene Breakdown milestone', () => {
  assert.match(progress, /hasCombinedSceneBreakdown = hasScenePlan && hasVisualPlan/);
  assert.match(progress, /hasScenePlan \|\| hasVisualPlan/);
  assert.doesNotMatch(progress, /Visual Planner/);
});

test('Scene Breakdown exposes pacing telemetry and section density context', () => {
  assert.match(panel, /scene-breakdown-pacing-metrics/);
  assert.match(panel, /Avg Beat/);
  assert.match(panel, /Median Beat/);
  assert.match(panel, /P90 Beat/);
  assert.match(panel, /Max Beat/);
  assert.match(panel, /Timing Policy/);
  assert.match(panel, /pacing_density/);
});

test('Scene Breakdown pacing fallback always includes max duration', () => {
  assert.match(panel, /type DurationStats = \{ average: number; median: number; p90: number; max: number \}/);
  assert.match(panel, /max: ordered\[ordered\.length - 1\]/);
});
