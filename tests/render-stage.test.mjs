import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const studio=readFileSync(new URL('../src/pages/StudioPage.tsx',import.meta.url),'utf8');
const panel=readFileSync(new URL('../src/features/studio/RenderPanel.tsx',import.meta.url),'utf8');
const client=readFileSync(new URL('../src/api/client.ts',import.meta.url),'utf8');
const progress=readFileSync(new URL('../src/features/projects/projectProgress.ts',import.meta.url),'utf8');

test('Render is a real stage immediately after Assembly',()=>{
  assert.ok(studio.indexOf("id: 'assembly'") < studio.indexOf("id: 'render'"));
  assert.match(progress,/'Assembly',[\s\S]*'Render'/);
  assert.doesNotMatch(studio,/id: 'analysis'/);
});

test('Render panel owns profile, render, rerender, progress, cancel, retry and browser playback',()=>{
  for (const token of ['render-profile','Render Video','Rerender','render-progress','cancel-render','retry-render','render-video-player','<video','<track']) assert.ok(panel.includes(token),token);
  assert.ok(panel.includes('lineageRef') && panel.includes('captured !== lineageRef.current'));
  assert.ok(panel.includes('No Export Package controls') && panel.includes('No thumbnail controls'));
});

test('Render panel keeps durable workspace and terminal failures visible',()=>{
  assert.match(panel,/refetchInterval:\s*\(query\)\s*=>\s*query\.state\.data\?\.active_operation\s*\?\s*750\s*:\s*false/);
  assert.ok(panel.includes('render-workspace-errors'));
  assert.ok(panel.includes('workspace.data?.errors'));
});

test('frontend sends only exact render identities and uses backend range video route',()=>{
  for (const token of ['render_profile_id','expected_assembly_plan_artifact_id','expected_subtitle_srt_artifact_id','expected_subtitle_vtt_artifact_id','expected_final_video_artifact_id','operation_type']) assert.ok(client.includes(token),token);
  for (const forbidden of ['ffmpeg_args:', 'filter_graph:', 'output_path:']) assert.ok(!client.includes(forbidden),forbidden);
  assert.match(client,/\/api\/v4\/projects\/\$\{projectId\}\/render\/video/);
});