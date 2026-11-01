import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile} from 'node:fs/promises';
import {withExclusiveReceipt} from './exclusive-receipt.mjs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const [mode,name='guarded-runtime-final']=process.argv.slice(2);
if(!/^guarded-runtime(?:-[a-z0-9-]+)?$/.test(name)) throw new Error('Fixed guarded-runtime receipt name required');
const receipt=new URL(`../results/${name}.json`,import.meta.url);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
if(mode==='--run') {
  await withExclusiveReceipt(receipt, async save => {
  const code=`import('/spike/probe-io.mjs').then(async({boundedJson})=>{const r=await fetch('http://127.0.0.1:8803',{method:'POST',signal:AbortSignal.timeout(60000)});console.log(JSON.stringify({status:r.status,body:await boundedJson(r)}))})`;
  let observation;
  try {const {stdout}=await promisify(execFile)('docker',['exec','bifrost-pyodide-ddf316c1','node','-e',code],
    {timeout:70000,maxBuffer:1024*1024});observation=JSON.parse(stdout);}
  catch(error){observation={observer_error_type:error.name,observation_timeout:!!error.killed};}
  const source_sha256={};
  for(const name of ['guarded-runtime-entry.mjs','wrangler.guarded-runtime.jsonc','guarded-runtime-probe.mjs',
    'runtime_guard.py','runtime-runner.py','pack-engine.py','exclusive-receipt.mjs']) {
    source_sha256[name]=hash(await readFile(new URL(name,import.meta.url)));
  }
  const manifest=JSON.parse(await readFile(new URL('../results/guarded-runtime-manifest.json',import.meta.url),'utf8'));
  await save({...observation,cloud_invocations:0,source_sha256,
    payload_sha256:manifest.payload_sha256});
  });
} else if(mode==='--verify') {
  const r=JSON.parse(await readFile(receipt,'utf8'));
  assert.equal(r.status,200);assert.equal(r.body.drift_rejected,true);
  assert.equal(r.body.upstream_worker_imported,true);assert.deepEqual(r.body.effects_after_rejection,[]);
  for(const [name,digest] of Object.entries(r.source_sha256)) assert.equal(hash(await readFile(new URL(name,import.meta.url))),digest);
  const manifest=JSON.parse(await readFile(new URL('../results/guarded-runtime-manifest.json',import.meta.url),'utf8'));
  assert.equal(manifest.payload_sha256,r.payload_sha256);
  assert.equal(manifest.files['runtime_guard.py'],r.source_sha256['runtime_guard.py']);
  assert.equal(manifest.files['runtime_runner.py'],r.source_sha256['runtime-runner.py']);
  console.log(r.body);
} else throw new Error('Use --run once or --verify');
