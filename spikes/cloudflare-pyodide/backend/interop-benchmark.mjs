// One fixed local invocation; --verify is offline and never runs Python.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile,writeFile,access} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const [mode]=process.argv.slice(2);
const receipt=new URL('../results/interop-local.json',import.meta.url);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
function verify(report) {
  assert.equal(report.status,200);
  assert.equal(report.body.correctness,true);
  assert.deepEqual(report.body.rows.map(r=>r.text_bytes),[128,4096,65536]);
  for(const row of report.body.rows) {
    for(const method of ['python_json_roundtrip','ffi_roundtrip','js_json_roundtrip']) {
      const values=row.samples_ms_per_operation[method];
      assert.equal(values.length,5);
      assert.ok(values.every(v=>typeof v==='number' && Number.isFinite(v) && v>=0));
      assert.equal(row.median_ms_per_operation[method],[...values].sort((a,b)=>a-b)[2]);
    }
  }
}
if(mode==='--run') {
  try {await access(receipt);throw new Error('Preserve existing receipt; invocation refused');}
  catch(error){if(error.code!=='ENOENT')throw error;}
  const started=Date.now();
  const code=`import('/spike/probe-io.mjs').then(async({boundedJson})=>{const r=await fetch('http://127.0.0.1:8801',{method:'POST',signal:AbortSignal.timeout(60000)});console.log(JSON.stringify({status:r.status,body:await boundedJson(r)}))})`;
  let observation;
  try {
    const {stdout}=await promisify(execFile)('docker',['exec','bifrost-pyodide-ddf316c1','node','-e',code],
      {timeout:70000,maxBuffer:1024*1024});
    observation=JSON.parse(stdout);
  } catch(error) {
    observation={observer_error_type:error.name,observation_timeout:!!error.killed};
  }
  const report={...observation,runtime:'local-workerd',wrangler:'4.156.0',
    started_ms:started,total_observer_wall_ms:Date.now()-started,
    cloud_invocations:0,cpu_ms:null,peak_memory_bytes:null,source_sha256:{}};
  for(const name of ['interop-entry.mjs','wrangler.interop.jsonc','interop-benchmark.mjs']) {
    report.source_sha256[name]=hash(await readFile(new URL(name,import.meta.url)));
  }
  await writeFile(receipt,JSON.stringify(report,null,2)+'\n',{flag:'wx'});
  verify(report);
  console.log('Local interop measurement captured and correctness checked');
} else if(mode==='--verify') {
  const report=JSON.parse(await readFile(receipt,'utf8'));verify(report);
  for(const [name,digest] of Object.entries(report.source_sha256)) assert.equal(hash(await readFile(new URL(name,import.meta.url))),digest);
  console.log(report.body.rows.map(r=>({text_bytes:r.text_bytes,...r.median_ms_per_operation})));
} else throw new Error('Use --run once or --verify');
