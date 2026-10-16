// Node-only P0 trust boundary: canonical containment + actual bounded decode.
// Never import this module into browser Player or Remotion JSX.
import {realpath, stat, open} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {safePath} from './plan.mjs';

export const LIMITS = Object.freeze({imageBytes:12*1024*1024,audioBytes:32*1024*1024,
  imageWidth:4096,imageHeight:4096,imagePixels:16*1024*1024,audioSeconds:300,probeTimeoutMs:12000});
const ERROR=(code,message)=>Object.assign(new Error(message),{code});
const run=(binary,args)=> {
  try{return execFileSync(binary,args,{encoding:'utf8',timeout:LIMITS.probeTimeoutMs,maxBuffer:512*1024,windowsHide:true});}
  catch(e){throw ERROR('media_decode',binary+' failed: '+(e.stderr?.toString()||e.message).slice(0,500));}
};
const imageFormat=buf=> {
  if(buf.length>=8&&buf.subarray(0,8).equals(Buffer.from('89504e470d0a1a0a','hex')))return 'png';
  if(buf.length>=3&&buf[0]===255&&buf[1]===216&&buf[2]===255)return 'jpeg';
  if(buf.length>=12&&buf.toString('ascii',0,4)==='RIFF'&&buf.toString('ascii',8,12)==='WEBP')return 'webp';
  return null;
};
const audioFormat=buf=> {
  if(buf.length>=12&&buf.toString('ascii',0,4)==='RIFF'&&buf.toString('ascii',8,12)==='WAVE')return 'wav';
  if(buf.length>=3&&buf.toString('ascii',0,3)==='ID3')return 'mp3';
  if(buf.length>=2&&buf[0]===255&&(buf[1]&0xe0)===0xe0)return 'mp3';
  return null;
};
export async function validateResolvedAssets(plan,publicRoot){
 const issues=[];
 const refs=[{path:'$.audioSrc',value:plan?.audioSrc,extensions:['wav','mp3'],type:'audio'}];
 if(Array.isArray(plan?.setups))for(const [si,s] of plan.setups.entries()){
   if(!Array.isArray(s?.events))continue;
   for(const [ei,e] of s.events.entries())if(e?.assetPath!==undefined)
     refs.push({path:`$.setups[${si}].events[${ei}].assetPath`,value:e.assetPath,extensions:['png','jpg','jpeg','webp'],type:'image'});
 }
 let root;
 try{root=await realpath(publicRoot);if(!(await stat(root)).isDirectory())throw Error('root is not a directory');}
 catch{issues.push({path:'$.publicRoot',code:'invalid_root',message:'Public asset root must be an existing directory'});return issues;}
 for(const ref of refs){
  if(ref.value===null||ref.value===undefined)continue;
  const extension=typeof ref.value==='string'?ref.value.split('.').at(-1)?.toLowerCase():'';
  if(!safePath(ref.value)||!ref.extensions.includes(extension)){
    issues.push({path:ref.path,code:'asset_path',message:'Unsafe path or unsupported extension'});continue;
  }
  let actual,meta;
  try{
   actual=await realpath(path.resolve(root,ref.value));
   if(!actual.startsWith(root+path.sep)){issues.push({path:ref.path,code:'asset_escape',message:'Symlink/path escape'});continue;}
   meta=await stat(actual);
   if(!meta.isFile()){issues.push({path:ref.path,code:'asset_type',message:'Media must be a regular file'});continue;}
  }catch{issues.push({path:ref.path,code:'asset_missing',message:'Missing or unreadable media file'});continue;}
  const max=ref.type==='image'?LIMITS.imageBytes:LIMITS.audioBytes;
  if(meta.size<1||meta.size>max){issues.push({path:ref.path,code:'asset_size',message:'Empty or oversized media'});continue;}
  try{
   // Signature is independent of the filename; decode checks the ENTIRE image/audio stream.
   const handle=await open(actual,'r');
   let sig;
   try{sig=Buffer.alloc(32);const result=await handle.read(sig,0,32,0);sig=sig.subarray(0,result.bytesRead);}
   finally{await handle.close();}
   const signature=ref.type==='image'?imageFormat(sig):audioFormat(sig);
   const wanted=extension==='jpg'?'jpeg':extension;
   if(!signature||signature!==wanted)throw ERROR('media_signature','Media header and extension mismatch');
   const data=JSON.parse(run('ffprobe',['-v','error','-show_entries',
      'stream=codec_name,width,height,channels,sample_rate:format=duration','-of','json',actual]));
   const stream=data.streams?.[0]||{};
   if(ref.type==='image'){
     const w=Number(stream.width),h=Number(stream.height);
     if(!Number.isSafeInteger(w)||!Number.isSafeInteger(h)||w<1||h<1||
        w>LIMITS.imageWidth||h>LIMITS.imageHeight||w*h>LIMITS.imagePixels)
       throw ERROR('image_dimensions','Invalid/unsafe image dimensions');
     if(!({png:['png'],jpeg:['mjpeg'],webp:['webp']}[signature]||[]).includes(stream.codec_name))
       throw ERROR('media_codec','Unsupported image codec');
     run('ffmpeg',['-nostdin','-v','error','-xerror','-err_detect','explode','-i',actual,'-frames:v','1','-f','null','-']);
   }else{
     const duration=Number(data.format?.duration);
     if(!Number.isFinite(duration)||duration<=0||duration>LIMITS.audioSeconds)
       throw ERROR('audio_duration','Invalid or unsafe duration');
     if(signature==='mp3'&&stream.codec_name!=='mp3')
       throw ERROR('media_codec','Invalid MP3 codec');
     if(signature==='wav'&&!String(stream.codec_name||'').startsWith('pcm_'))
       throw ERROR('media_codec','Unsupported WAV codec');
     run('ffmpeg',['-nostdin','-v','error','-xerror','-err_detect','explode','-i',actual,'-map','0:a:0','-f','null','-']);
   }
  }catch(err){issues.push({path:ref.path,code:err.code||'media_decode',message:String(err.message).slice(0,600)});}
 }
 return issues;
}
