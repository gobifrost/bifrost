// Local qualification only. Explicit --run; --verify never invokes a Worker.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile,writeFile,access} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
const exec=promisify(execFile);
const [mode,name='canonical-isolation',...extra]=process.argv.slice(2);
const historicalCommit=mode==='--verify' && extra.length===2 && extra[0]==='--commit' && /^[a-f0-9]{40}$/.test(extra[1]) ? extra[1] : null;
const prewarm=mode==='--run' && extra.length===1 && extra[0]==='--prewarm';
if(!['--run','--verify'].includes(mode) || (extra.length && !prewarm && !historicalCommit) || !/^canonical-isolation(?:-[a-z0-9-]+)?$/.test(name)) {
  throw new Error('Use --run or --verify, optional canonical-isolation-<name>; --run also accepts --prewarm');
}
const receipt=new URL(`../results/${name}.json`,import.meta.url);
const repo=new URL('../../../',import.meta.url);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
function verify(report) {
  assert.equal(report.process_exit_code,0);
  assert.equal(report.participants.length,2);
  assert.equal(report.barrier_keys_remaining,0);
  assert.equal(report.physical_termination_verified,false);
  assert.equal(report.cloud_invocations,0);
  const [a,b]=report.participants.map(item=>item.report);
  assert.notEqual(a.organization_id,b.organization_id);
  assert.equal(a.peer_organization_id,b.organization_id);
  assert.equal(b.peer_organization_id,a.organization_id);
  assert.equal(new Set([a.result.result.execution_id,b.result.result.execution_id]).size,2);
  assert.ok(Math.min(a.provider_end_ms,b.provider_end_ms)>Math.max(a.provider_start_ms,b.provider_start_ms),
    'Provider request intervals must overlap');
  for(const item of report.participants) {
    assert.equal(item.process_exit_code,0);
    const r=item.report;
    assert.equal(r.isolation_label,item.label);
    assert.equal(r.rendezvous_batch,report.batch);
    for(const field of ['canonical_claim_verified','canonical_terminal_consumption_verified',
      'lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified']) assert.equal(r[field],true);
    assert.equal(r.launches,1);
    assert.ok(r.worker_heartbeat_count>=3,'Native worker startup/claim/reconnect heartbeats required');
    assert.equal(r.cross_tenant_status,403);
    assert.equal(r.credential_revoked_after_terminal_status,401);
    const value=r.result.result.result;
    assert.equal(value.tenant_label,item.label);
    assert.equal(value.peer_sdk_status,403);
    assert.equal(value.value,item.label==='tenant-a' ? 70 : 78);
    assert.equal(r.independent_document_value,value.value);
    assert.equal(value.parent_secret_present,false);
    assert.equal(value.credential_redaction_probe,'[REDACTED]');
    for(const key of ['builtins_before','environment_before','module_before','tmp_before']) assert.equal(value[key],null);
  }
}
if(mode==='--verify') {
  const report=JSON.parse(await readFile(receipt,'utf8'));
  verify(report);
  for(const [path,digest] of Object.entries(report.source_sha256)) {
    const bytes=historicalCommit ? (await exec('git',['show',`${historicalCommit}:${path}`],
      {cwd:repo,encoding:'buffer',maxBuffer:2*1024*1024})).stdout : await readFile(new URL(path,repo));
    assert.equal(hash(bytes),digest,path);
  }
  console.log('Concurrent real local tenant/attempt/SDK/isolate qualification verified; no cloud or physical-stop acceptance');
} else {
  let exists=true;
  try {await access(receipt);} catch(error) {if(error.code!=='ENOENT') throw error;exists=false;}
  if(exists) throw new Error('Receipt already exists; preserve it and choose a new explicit name before any invocation');
  let warmup;
  if(prewarm) {
    try {
      const code=`fetch('http://127.0.0.1:8799/__fixture_warmup',{method:'POST',signal:AbortSignal.timeout(30000)}).then(async r=>{if(!r.ok)throw new Error('Warmup failed');const b=await r.json();if(b.worker_imported!==true)throw new Error('Warmup not verified');console.log(JSON.stringify({worker_imported:true}))}).catch(()=>process.exitCode=1)`;
      const {stdout}=await exec('docker',['exec','bifrost-pyodide-ddf316c1','node','-e',code],
        {maxBuffer:1024*1024,timeout:40000});
      warmup=JSON.parse(stdout);
    } catch(error) {
      await writeFile(receipt,JSON.stringify({process_exit_code:1,phase:'credential-free-warmup',
        error_type:error.name,cloud_invocations:0},null,2)+'\n',{flag:'wx'});
      throw error;
    }
  }
  const batch=randomUUID().replaceAll('-','');
  const common=['exec','bifrost-pyodide-test-tools-ddf316c1','env',
    'LOG_DIR=/tmp/bifrost-bifrost-test-79c5c14e','BIFROST_TEST_IMAGE_TAG=bifrost-test-79c5c14e',
    'docker','compose','-p','bifrost-test-79c5c14e','-f','docker-compose.test.yml','--profile','test',
    'run','--rm','--no-deps','--volume',
    '/run/desktop/mnt/host/c/Users/ThomasBray/.t3/worktrees/bifrost/t3-ddf316c1/spikes/cloudflare-pyodide:/spike:ro'];
  async function run(label) {
    const container=`bifrost-pyodide-isolation-${batch}-${label}`;
    let stdout='',code=0,unknown=false;
    try {({stdout}=await exec('docker',[...common,'--name',container,'test-runner','python',
      '/spike/backend/canonical-local.py','--isolation-label',label,'--batch',batch],
      {maxBuffer:2*1024*1024,timeout:180000}));}
    catch(error) {stdout=error.stdout ?? '';code=typeof error.code==='number' ? error.code : 1;unknown=!!error.killed;}
    const line=stdout.split(/\r?\n/).find(line=>line.startsWith('{"runtime":'));
    let parsed;
    try {parsed=line ? JSON.parse(line) : {error:'No sanitized qualification report returned'};}
    catch {parsed={error:'Malformed sanitized qualification report'};code=1;}
    if(!line) code=code || 1;
    return {label,container,process_exit_code:code,unknown_process_state:unknown,
      report:parsed};
  }
  const participants=await Promise.all(['tenant-a','tenant-b'].map(run));
  const report={process_exit_code:participants.some(p=>p.process_exit_code!==0) ? 1 : 0,
    batch,participants,prewarm,warmup,cloud_invocations:0,physical_termination_verified:false};
  // Preserve terminal actor receipts even if subsequent cleanup/verification fails.
  await writeFile(receipt,JSON.stringify(report,null,2)+'\n',{flag:'wx'});
  try {
  if(!participants.some(p=>p.unknown_process_state)) {
    const cleanup=`import asyncio,json,sys
from src.core.cache import get_redis
async def clean():
    batch=sys.argv[1]
    assert len(batch)==32 and all(c in '0123456789abcdef' for c in batch)
    keys=[f'bifrost:pyodide-isolation:{batch}:{phase}:{label}' for phase in ('setup','prepared','effects_done','abort') for label in ('tenant-a','tenant-b')]
    async with get_redis() as redis:
        await redis.delete(*keys)
        print(json.dumps({'remaining':await redis.exists(*keys)}))
asyncio.run(clean())`;
    const {stdout}=await exec('docker',[...common,'test-runner','python','-c',cleanup,batch],
      {maxBuffer:1024*1024,timeout:30000});
    report.barrier_keys_remaining=JSON.parse(stdout.split(/\r?\n/).find(line=>line.startsWith('{"remaining":'))).remaining;
  } else report.cleanup_deferred_until_exact_container_reconciliation=true;
  } catch(error) {
    report.process_exit_code=1;
    report.cleanup_error_type=error.name;
  }
  const paths=['spikes/cloudflare-pyodide/backend/canonical-isolation.mjs',
    'spikes/cloudflare-pyodide/backend/fixture-admission.mjs',
    'spikes/cloudflare-pyodide/backend/runtime-runner.py','spikes/cloudflare-pyodide/backend/runtime_guard.py',
    'spikes/cloudflare-pyodide/backend/pack-engine.py',
    'spikes/cloudflare-pyodide/backend/canonical-local.py','spikes/cloudflare-pyodide/backend/authorized-local-entry.mjs',
    'spikes/cloudflare-pyodide/backend/runtime-code.mjs','spikes/cloudflare-pyodide/backend/attempt_journal.py'];
  report.source_sha256=Object.fromEntries(await Promise.all(paths.map(async path=>[path,hash(await readFile(new URL(path,repo)))])));
  let verificationError;
  try {verify(report);} catch(error) {verificationError=error;report.process_exit_code=1;report.verification_error_type=error.name;}
  await writeFile(receipt,JSON.stringify(report,null,2)+'\n');
  if(verificationError) throw verificationError;
  console.log({process_exit_code:0,local_fresh_runs:2,tenant_sdk_denials:[403,403],
    values:participants.map(p=>p.report.independent_document_value),barrier_keys_remaining:report.barrier_keys_remaining});
}
