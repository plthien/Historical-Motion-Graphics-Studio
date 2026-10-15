import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
const read=(p)=>readFileSync(resolve(process.cwd(),p),'utf8');
const panel=read('src/features/studio/AssemblyPanel.tsx');
const studio=read('src/pages/StudioPage.tsx');
const client=read('src/api/client.ts');
const progress=read('src/features/projects/projectProgress.ts');

test('Assembly is a separate deterministic stage after Voice',()=>{
 const voice=studio.indexOf("id: 'voice'"); const assembly=studio.indexOf("id: 'assembly'");
 assert.ok(voice>=0 && assembly>voice); assert.match(studio,/<AssemblyPanel/); assert.match(progress,/'Voice',[\s\S]*'Assembly'/);
 assert.match(progress,/assembly_plan_artifact_id/); assert.match(progress,/subtitle_srt_artifact_id/); assert.match(progress,/subtitle_vtt_artifact_id/);
});
test('Assembly panel exposes backend-owned build review subtitle and approval controls',()=>{
 for (const value of ['Build Assembly','Rebuild','Approve Assembly','Download SRT','Download VTT','assembly-timeline-preview','assembly-subtitle-preview','assembly-qc','Optional explicit audio','image']) assert.match(panel,new RegExp(value));
 assert.doesNotMatch(panel,/ffmpeg|filter graph|subtitle edit|draggable|asset_strategy|generated_still/i);
 assert.match(panel,/apiClient\.getAssemblyWorkspace/); assert.match(panel,/apiClient\.buildAssembly/); assert.match(panel,/apiClient\.approveAssembly/);
});
test('frontend sends only exact identities and optional bindings',()=>{
 for (const field of ['expected_scene_plan_artifact_id','expected_scene_asset_manifest_artifact_id','expected_narration_audio_artifact_id','expected_narration_word_timing_map_artifact_id','expected_beat_timing_map_artifact_id']) assert.match(panel,new RegExp(field));
 for (const method of ['getAssemblyWorkspace','buildAssembly','approveAssembly','getAssemblySrtUrl','getAssemblyVttUrl']) assert.match(client,new RegExp(method));
 assert.doesNotMatch(panel,/subtitle timestamp|selected_visual_artifact_id\s*:|camera_movement\s*:/i);
});

test('Task 4 hard-cuts Assembly to project-owned timing authorities',()=>{
 const service=read('app/modules/v4/assembly_service.py');
 const subtitles=read('app/modules/v4/assembly_subtitles.py');
 assert.doesNotMatch(service,/VoiceService|_load_current_pair/);
 assert.match(service,/load_narration_word_timing_map/);
 assert.match(service,/load_beat_timing_map/);
 assert.match(service,/expected_narration_word_timing_map_artifact_id/);
 assert.match(subtitles,/word_timing_map/);
 assert.match(subtitles,/beat_timing_map/);
 assert.doesNotMatch(subtitles,/timing_map\["segments"\]/);
 assert.match(studio,/narration_word_timing_map_artifact_id && project\.beat_timing_map_artifact_id/);
});
