import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile} from 'node:fs/promises';
import {withExclusiveReceipt} from './exclusive-receipt.mjs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const [mode]=process.argv.slice(2);
const receipt=new URL('../results/runtime-fingerprint.json',import.meta.url);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
if(mode==='--run') {
  await withExclusiveReceipt(receipt, async save => {
  const code=`import('/spike/probe-io.mjs').then(async({boundedJson})=>{const r=await fetch('http://127.0.0.1:8802',{method:'POST',signal:AbortSignal.timeout(60000)});console.log(JSON.stringify({status:r.status,body:await boundedJson(r)}))})`;
  let observation;
  try {const {stdout}=await promisify(execFile)('docker',['exec','bifrost-pyodide-ddf316c1','node','-e',code],
    {timeout:70000,maxBuffer:1024*1024});observation=JSON.parse(stdout);}
  catch(error){observation={observer_error_type:error.name,observation_timeout:!!error.killed};}
  const source_sha256={};
  for(const name of ['runtime-probe-entry.mjs','wrangler.runtime-probe.jsonc','runtime-code.mjs','runtime-probe.mjs','exclusive-receipt.mjs']) {
    source_sha256[name]=hash(await readFile(new URL(name,import.meta.url)));
  }
  await save({...observation,cloud_invocations:0,source_sha256});
  });
} else if(mode==='--verify') {
  const r=JSON.parse(await readFile(receipt,'utf8'));
  assert.equal(r.status,200);assert.equal(r.body.invalid_rejected,true);
  assert.deepEqual(r.body.validated,{values:[1,2]});
  for(const [name,digest] of Object.entries(r.source_sha256)) assert.equal(hash(await readFile(new URL(name,import.meta.url))),digest);
  console.log(r.body);
} else throw new Error('Use --run once or --verify');
