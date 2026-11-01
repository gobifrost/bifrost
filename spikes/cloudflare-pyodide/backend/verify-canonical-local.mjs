// Offline receipt/provenance verification; never invokes a Worker or API.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const args=process.argv.slice(2);
if(args.length && (args.length!==2 || args[0]!=='--commit' || !/^[a-f0-9]{40}$/.test(args[1]))) {
  throw new Error('Use no arguments for current source or --commit <full tested SHA>');
}
const historicalCommit=args[1];
const exec=promisify(execFile);
const results=new URL('../results/',import.meta.url);
const repo=new URL('../../../',import.meta.url);
const read=async name=>JSON.parse(await readFile(new URL(name,results),'utf8'));
const sourceBytes=async path=>historicalCommit
  ? (await exec('git',['show',`${historicalCommit}:${path}`],{cwd:repo,encoding:'buffer',maxBuffer:2*1024*1024})).stdout
  : readFile(new URL(path,repo));
const report=await read('canonical-local.json');
assert.equal(report.process_exit_code,0);
for(const field of ['canonical_claim_verified','canonical_terminal_consumption_verified',
  'lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified',
  'completed_claim_retained_as_upstream_specifies']) assert.equal(report[field],true,field);
assert.equal(report.launches,1);
assert.equal(report.persisted_log_count,1);
assert.equal(report.cross_tenant_status,403);
assert.equal(report.credential_revoked_after_terminal_status,401);
assert.equal(report.cloud_invocations,0);
assert.equal(report.physical_termination_verified,false);
const envelope=report.result.result;
assert.equal(envelope.success,true);
assert.equal(envelope.result.value,70);
assert.equal(envelope.result.parent_secret_present,false);
assert.equal(envelope.result.credential_redaction_probe,'[REDACTED]');
assert.equal(report.independent_document_value,70);
assert.ok(Object.values(report.workspace_sha256).includes(envelope.metrics.code_hash));
assert.ok(report.result.api_calls.some(call=>call.path==='/api/sdk/runtime/logs' && call.status===200));
const provenance=await read('canonical-local-provenance.json');
for(const [path,digest] of Object.entries(provenance.source_sha256)) {
  assert.equal(createHash('sha256').update(await sourceBytes(path)).digest('hex'),digest,path);
}
const manifest=await read('canonical-local-runtime-manifest.json');
const sources=await read('canonical-local-source.json');
assert.equal(manifest.files['src/services/execution/result_envelope.py'],
  sources.source_files['src/services/execution/result_envelope.py']);
for(const [path,digest] of Object.entries(sources.source_files)) {
  assert.equal(createHash('sha256').update(await sourceBytes('api/'+path)).digest('hex'),digest,path);
}
const cleanup=await read('canonical-local-cleanup.json');
assert.equal(cleanup.listener_status,'ECONNREFUSED');
assert.equal(cleanup.test_network_attached,false);
console.log('Canonical local claim/engine/SDK/envelope/completion/reconnect receipt verified; cloud/physical stop remain unproven');
