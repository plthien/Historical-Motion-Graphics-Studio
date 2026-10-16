import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import {mkdtemp,mkdir,writeFile,symlink,rm,readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {motionPlan} from '../src/plan.mjs';
import {LIMITS,validateResolvedAssets} from '../src/asset-safety.mjs';
import {imageBytes} from './media-fixtures.mjs';
const clone=()=>structuredClone(motionPlan);
const planWith=(name)=>{const p=clone();p.setups[2].events[0].assetPath=name;return p;};
const make=()=>mkdtemp(path.join(os.tmpdir(),'p0-assets-'));
test('real image signature and full FFmpeg decode: PNG, JPEG and WebP PASS',async()=>{
 const root=await make();
 try{
  for(const [format,extension] of [['png','png'],['jpeg','jpg'],['webp','webp']]){
    await writeFile(path.join(root,'valid.'+extension),imageBytes(format));
    assert.deepEqual(await validateResolvedAssets(planWith('valid.'+extension),root),[],format);
  }
 }finally{await rm(root,{recursive:true,force:true});}
});
test('text disguised as PNG, truncated file, wrong extension and empty file fail',async()=>{
 const root=await make();
 try{
  const png=imageBytes('png');
  await writeFile(path.join(root,'fake.png'),'placeholder');
  await writeFile(path.join(root,'trunc.png'),png.subarray(0,31));
  await writeFile(path.join(root,'wrong.jpg'),png);
  await writeFile(path.join(root,'empty.png'),Buffer.alloc(0));
  for(const file of ['fake.png','trunc.png','wrong.jpg','empty.png']){
   const errors=await validateResolvedAssets(planWith(file),root);
   assert.ok(errors.length>0,file);
  }
 }finally{await rm(root,{recursive:true,force:true});}
});
test('invalid/unsafe image dimensions and file-size cap reject early',async()=>{
 const root=await make();
 try{
  const png=imageBytes('png'),changed=Buffer.from(png);
  changed.writeUInt32BE(0,16); // invalid width, so ffprobe or ffmpeg must reject
  await writeFile(path.join(root,'zero.png'),changed);
  const errors=await validateResolvedAssets(planWith('zero.png'),root);
  assert.ok(errors.length>0);
  await writeFile(path.join(root,'huge.png'),Buffer.alloc(LIMITS.imageBytes+1));
  assert.equal((await validateResolvedAssets(planWith('huge.png'),root))[0].code,'asset_size');
 }finally{await rm(root,{recursive:true,force:true});}
});
test('local path, symlink escape, directory, missing, unsupported and invalid public root fail',async()=>{
 const root=await make(),external=await make();
 try{
  await mkdir(path.join(root,'media'));
  for(const file of ['missing.png','media','../bad.png','/bad.png','https://x/a.png','bad.svg']){
    assert.ok((await validateResolvedAssets(planWith(file),root)).length>0,file);
  }
  if(process.platform!=='win32'){
    await writeFile(path.join(external,'outside.png'),imageBytes('png'));
    await symlink(external,path.join(root,'leak'));
    assert.equal((await validateResolvedAssets(planWith('leak/outside.png'),root))[0].code,'asset_escape');
  }
  assert.equal((await validateResolvedAssets(clone(),path.join(root,'invalid')))[0].code,'invalid_root');
 }finally{await rm(root,{recursive:true,force:true});await rm(external,{recursive:true,force:true});}
});
test('corrupt audio is rejected, real PCM WAV passes, no-media technical plan remains valid',async()=>{
 const root=await make();
 try{
  assert.deepEqual(await validateResolvedAssets(clone(),root),[]);
  await writeFile(path.join(root,'bad.wav'),'placeholder');
  let p=clone();p.audioSrc='bad.wav';
  assert.ok((await validateResolvedAssets(p,root)).length>0);
  execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','anullsrc=channel_layout=mono:sample_rate=44100','-t','0.2','-c:a','pcm_s16le','-y',path.join(root,'good.wav')],{timeout:12000});
  p.audioSrc='good.wav';
  assert.deepEqual(await validateResolvedAssets(p,root),[]);
 }finally{await rm(root,{recursive:true,force:true});}
});
