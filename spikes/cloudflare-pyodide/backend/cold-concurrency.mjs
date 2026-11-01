// Invoke only the existing local fixture once. No cloud API or credentials.
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile,writeFile,access} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const [mode]=process.argv.slice(2);
const output=new URL('../results/cold-concurrency-minimal.json',import.meta.url);
if(mode==='--run') {
  try {await access(output);throw new Error('Preserve existing receipt; no repeat invocation');}
  catch(error){if(error.code!=='ENOENT')throw error;}
  const code=`fetch('http://127.0.0.1:8800',{method:'POST',signal:AbortSignal.timeout(60000)}).then(async r=>{const text=await r.text();if(text.length>65536)throw new Error('Oversized fixed fixture response');console.log(JSON.stringify({status:r.status,body:JSON.parse(text)}))}).catch(e=>{console.log(JSON.stringify({error_type:e.name}));process.exitCode=1})`;
  const started=Date.now();
  let result,exit=0;
  try {const {stdout}=await promisify(execFile)('docker',['exec','bifrost-pyodide-ddf316c1','node','-e',code],
    {timeout:70000,maxBuffer:1024*1024});result=JSON.parse(stdout);}
  catch(error){exit=1;result={error_type:error.name,observation_timeout:!!error.killed};}
  const source_sha256={};
  for(const name of ['cold-concurrency-entry.mjs','wrangler.cold-concurrency.jsonc','cold-concurrency.mjs']) {
    source_sha256[name]=createHash('sha256').update(await readFile(new URL(name,import.meta.url))).digest('hex');
  }
  await writeFile(output,JSON.stringify({runtime:'local-workerd',wrangler:'4.156.0',
    started_ms:started,wall_ms:Date.now()-started,observer_exit:exit,result,source_sha256,
    cloud_invocations:0,credentials:false,bifrost_modules:false},null,2)+'\n',{flag:'wx'});
  console.log({observer_exit:exit,result});
} else if(mode==='--verify') {
  const report=JSON.parse(await readFile(output,'utf8'));
  assert.equal(report.observer_exit,0);
  assert.equal(report.result.status,200);
  assert.equal(report.result.body.participants.length,2);
  for(const [name,digest] of Object.entries(report.source_sha256)) {
    assert.equal(createHash('sha256').update(await readFile(new URL(name,import.meta.url))).digest('hex'),digest);
  }
  console.log(report.result.body.participants);
} else throw new Error('Use --run once or --verify');
