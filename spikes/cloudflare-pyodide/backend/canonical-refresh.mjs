// Fixed local canonical SDK-refresh qualification; no cloud access.
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile} from 'node:fs/promises';
import {withExclusiveReceipt} from './exclusive-receipt.mjs';
import {createHash,randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
const exec=promisify(execFile),[mode,name='canonical-refresh-final',scenario='direct',
  artifactManifest='refresh-runtime-manifest.json']=process.argv.slice(2);
if(!/^[a-z0-9-]+-manifest\.json$/.test(artifactManifest)) throw new Error('Fixed artifact manifest filename required');
if(!['direct','challenge','concurrent','interruption','failure','transport','response-loss','retained-response','control-auth'].includes(scenario)) throw new Error('Unknown fixed qualification scenario');
if(!/^canonical-refresh(?:-[a-z0-9-]+)?$/.test(name)) throw new Error('Fixed canonical-refresh receipt name required');
const receipt=new URL(`../results/${name}.json`,import.meta.url);
const repo=new URL('../../../',import.meta.url);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
if(mode==='--run') {
  await withExclusiveReceipt(receipt, async save => {
  const health=await exec('docker',['inspect','bifrost-test-79c5c14e-api-1','--format','{{.State.Health.Status}}']);
  assert.equal(health.stdout.trim(),'healthy','Existing isolated API must be healthy before qualification');
  const container=`bifrost-pyodide-refresh-${randomUUID()}`;
  const report={container,scenario,phase:'invoking',cloud_invocations:0,physical_termination_verified:false};
  await save(report);
  let stdout='',code=0;
  try {({stdout}=await exec('docker',['exec','bifrost-pyodide-test-tools-ddf316c1','env',
    'LOG_DIR=/tmp/bifrost-bifrost-test-79c5c14e','BIFROST_TEST_IMAGE_TAG=bifrost-test-79c5c14e',
    'docker','compose','-p','bifrost-test-79c5c14e','-f','docker-compose.test.yml','--profile','test',
    'run','--rm','--no-deps','--name',container,'--volume',
    '/run/desktop/mnt/host/c/Users/ThomasBray/.t3/worktrees/bifrost/t3-ddf316c1/spikes/cloudflare-pyodide:/spike:ro',
    'test-runner','python',scenario==='control-auth' ? '/spike/backend/control-auth.py' :
      ['response-loss','retained-response'].includes(scenario) ? '/spike/backend/provider-response-loss.py' : '/spike/backend/canonical-local.py',
    {challenge:'--expired-access',direct:'--refresh-probe',concurrent:'--concurrent-refresh',interruption:'--interrupt-refresh',failure:'--failure-probe',transport:'--transport-waiter','response-loss':'--response-loss','retained-response':'--retained-response','control-auth':'--control-auth'}[scenario]],
    {timeout:180000,maxBuffer:2*1024*1024}));}
  catch(error){stdout=error.stdout ?? '';code=typeof error.code==='number' ? error.code : 1;report.unknown_process_state=!!error.killed;}
  const lines=stdout.split(/\r?\n/).filter(line=>line.startsWith('{"runtime":'));
  const observations=lines.map(line=>JSON.parse(line));
  const best=observations.toReversed().find(r=>r.canonical_terminal_consumption_verified) ??
    (code===0 ? observations.at(-1) : observations[0]);
  report.phase='observed';report.process_exit_code=code;
  report.result=best ?? {error:'No sanitized report returned'};
  if(code!==0 && observations.length) report.qualification_error=observations.at(-1);
  report.source_sha256={};
  for(const path of ['spikes/cloudflare-pyodide/backend/canonical-refresh.mjs',
    'spikes/cloudflare-pyodide/backend/exclusive-receipt.mjs',
    'spikes/cloudflare-pyodide/backend/canonical-local.py','spikes/cloudflare-pyodide/backend/runtime-runner.py',
    'spikes/cloudflare-pyodide/backend/runtime_guard.py','spikes/cloudflare-pyodide/backend/authorized-local-entry.mjs',
    'spikes/cloudflare-pyodide/backend/test-api-route.mjs','spikes/cloudflare-pyodide/backend/pyodide_refresh.py',
    'spikes/cloudflare-pyodide/backend/pack-engine.py','spikes/cloudflare-pyodide/backend/refresh-gate.mjs',
    'spikes/cloudflare-pyodide/backend/fixture-admission.mjs']) {
    report.source_sha256[path]=hash(await readFile(new URL(path,repo)));
  }
  report.source_sha256['spikes/cloudflare-pyodide/backend/attempt_journal.py']=hash(
    await readFile(new URL('spikes/cloudflare-pyodide/backend/attempt_journal.py',repo)));
  if(['response-loss','retained-response'].includes(scenario)) report.source_sha256['spikes/cloudflare-pyodide/backend/provider-response-loss.py']=hash(
    await readFile(new URL('spikes/cloudflare-pyodide/backend/provider-response-loss.py',repo)));
  if(scenario==='control-auth') report.source_sha256['spikes/cloudflare-pyodide/backend/control-auth.py']=hash(
    await readFile(new URL('spikes/cloudflare-pyodide/backend/control-auth.py',repo)));
  if(['retained-response','control-auth'].includes(scenario)) for(const path of ['api/src/worker/remote/launcher.py',
    'api/src/worker/remote/host.py','api/src/worker/remote/protocol.py','api/src/worker/remote/ownership.py',
    'api/src/worker/remote/fencing.py','api/src/jobs/consumers/workflow_execution.py',
    'api/src/services/execution/process_pool.py','api/src/services/worker_pool/claims.py',
    'api/src/worker/pool_main.py']) report.source_sha256[path]=hash(await readFile(new URL(path,repo)));
  report.runtime_manifest=scenario==='control-auth' ? null : artifactManifest;
  report.payload_sha256=scenario==='control-auth' ? null :
    JSON.parse(await readFile(new URL('../results/'+artifactManifest,import.meta.url),'utf8')).payload_sha256;
  await save(report);
  console.log({process_exit_code:code,refresh_probe:report.result.refresh_probe,
    workflow_document_status:report.result.workflow_document_status,
    worker_error_type:report.result.provider_evidence?.result?.error_type});
  process.exitCode=code || (best ? 0 : 1);
  });
} else if(mode==='--verify') {
  const r=JSON.parse(await readFile(receipt,'utf8'));
  assert.equal(r.phase,'observed');
  assert.equal(r.result[{challenge:'expired_access_probe',direct:'refresh_probe',concurrent:'concurrent_refresh_probe',
    interruption:'refresh_interruption_probe',failure:'workflow_failure_probe',transport:'transport_waiter_probe','response-loss':'provider_response_loss_probe','retained-response':'provider_retained_response_probe','control-auth':'control_auth_probe'}[scenario]],true);
  assert.notEqual(r.unknown_process_state,true);
  for(const [path,digest] of Object.entries(r.source_sha256)) assert.equal(hash(await readFile(new URL(path,repo))),digest);
  console.log({process_exit_code:r.process_exit_code,provider_evidence:r.result.provider_evidence,
    workflow_document_status:r.result.workflow_document_status});
} else throw new Error('Use --run once or --verify');
