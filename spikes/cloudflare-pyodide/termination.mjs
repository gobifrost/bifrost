import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {writeFile} from 'node:fs/promises';
const exec=promisify(execFile);
// This script restarts ONLY the named disposable spike container.
const started=Date.now();
const pending=fetch('http://127.0.0.1:8793',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({workload:'termination_probe'}),signal:AbortSignal.timeout(15000)})
  .then(async r=>({status:r.status,body:await r.text()})).catch(e=>({error:String(e)}));
await new Promise(resolve=>setTimeout(resolve,500));
await exec('docker',['restart','bifrost-pyodide-ddf316c1']);
const outcome=await pending;
assert.ok(outcome.error || outcome.status>=500);
await writeFile('results/termination.json',JSON.stringify({runtime:'local-workerd',
  test:'container termination during ten-second I/O',wall_ms:Date.now()-started,outcome,
  durable_recovery:'not implemented; no authoritative execution was claimed'},null,2)+'\n');
console.log('In-flight request interrupted; no durable recovery claim.');
