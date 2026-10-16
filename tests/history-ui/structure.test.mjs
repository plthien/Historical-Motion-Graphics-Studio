import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=(file)=>readFileSync(file,'utf8');
test('History source ships exactly seven stage identifiers without retired business panels',()=>{
 const code=read('src/features/history/api.ts');
 const names=[...code.matchAll(/\{key:"([^"]+)",label:/g)].map(x=>x[1]);
 assert.deepEqual(names,['topic','script_analysis','voice','assets','visual_director','motion_timeline','final_video']);
 const app=read('src/App.tsx');
 assert.ok(app.includes('api.channelCreate')&&app.includes('api.projectCreate')&&app.includes('api.status'));
 assert.ok(app.includes('disabled'));
 for(const file of ['src/App.tsx','src/features/history/api.ts']){
  const text=read(file);
  for(const old of ['scene_assets','scene_breakdown','visual_reference','prompt_bundle','/api/v4','youtube-ai-studio'])assert.ok(!text.includes(old),old);
 }
});
test('one real frontend boot and exact history API routes',()=>{
 assert.ok(read('index.html').includes('src/main.tsx'));
 const client=read('src/features/history/api.ts');
 for(const route of ['/api/history/channels','/api/history/projects'])assert.ok(client.includes(route));
 assert.ok(!client.includes('generateVideo'));
});
