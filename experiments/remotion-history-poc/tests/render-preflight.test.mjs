import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {motionPlan,CANONICAL_PLAN_BYTES} from '../src/plan.mjs';
import {preflight,renderWithPreflight,RENDER_ARGS,CANONICAL_PLAN_SHA256,launchRemotion} from '../scripts/render.mjs';
import {imageBytes} from './media-fixtures.mjs';
const temp=()=>mkdtemp(path.join(os.tmpdir(),'p0-render-'));
const mediaPlan=filename=>{const p=structuredClone(motionPlan);p.setups[2].events[0].kind='photo';p.setups[2].events[0].assetPath=filename;return p;};
test('canonical P0 render identity and no-media preflight always match',async()=>{
 const root=await temp();let calls=0;
 try{
  const approved=JSON.parse(CANONICAL_PLAN_BYTES);
  const a=await renderWithPreflight({plan:approved,publicRoot:root,execute:async({plan,planSha256})=>{
     calls++;assert.deepEqual(plan,approved);assert.equal(planSha256,CANONICAL_PLAN_SHA256);
  }});
  assert.equal(calls,1);
  assert.equal(a.planSha256,CANONICAL_PLAN_SHA256);
  assert.deepEqual(await preflight(motionPlan,root),{schemaVersion:'history_motion_poc.v1',assetsValidated:0});
 }finally{await rm(root,{recursive:true,force:true});}
});
test('valid alternate media plan can be independently probed but cannot silently render canonical plan',async()=>{
 const root=await temp();let calls=0;
 try{
  await writeFile(path.join(root,'valid.png'),imageBytes('png'));
  const alt=mediaPlan('valid.png');
  assert.equal((await preflight(alt,root)).assetsValidated,1);
  await assert.rejects(()=>renderWithPreflight({plan:alt,publicRoot:root,execute:async()=>{calls++;}}),/P0_PLAN_IDENTITY_MISMATCH/);
  assert.equal(calls,0);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('invalid alternate, corrupt content and mutated snapshot never start the renderer',async()=>{
 const root=await temp();let calls=0;
 try{
  const alt=structuredClone(motionPlan);alt.setups[0].events[0].text='Other headline';
  await assert.rejects(()=>renderWithPreflight({plan:alt,publicRoot:root,execute:async()=>{calls++;}}),/P0_PLAN_IDENTITY_MISMATCH/);
  alt.setups[0].events[0].durationFrames=-3;
  await assert.rejects(()=>renderWithPreflight({plan:alt,publicRoot:root,execute:async()=>{calls++;}}),/P0_PLAN_IDENTITY_MISMATCH/);
  await writeFile(path.join(root,'bad.png'),'corrupt');
  await assert.rejects(()=>preflight(mediaPlan('bad.png'),root),/MOTION_MEDIA_INVALID/);
  assert.equal(calls,0);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('canonical fixture is deeply immutable; CLI Root and Player both import it',async()=>{
 assert.ok(Object.isFrozen(motionPlan)&&Object.isFrozen(motionPlan.setups[0].events[0]));
 assert.throws(()=>{motionPlan.setups[0].events[0].text='MUTATED';},TypeError);
 assert.equal(JSON.stringify(motionPlan),CANONICAL_PLAN_BYTES);
 assert.equal(RENDER_ARGS.includes('HistoryPOC'),true);
 await assert.rejects(()=>launchRemotion({plan:motionPlan,planSha256:'wrong',publicRoot:'whatever'}),/P0_PLAN_IDENTITY_MISMATCH/);
});
test('invalid root fails closed before starting real Chromium',async()=>{
 await assert.rejects(()=>preflight(motionPlan,path.join(os.tmpdir(),'p0-not-exist-'+process.pid)),/invalid_root/);
});
