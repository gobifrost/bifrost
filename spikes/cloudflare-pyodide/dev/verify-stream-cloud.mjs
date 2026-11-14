import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const options = process.argv.slice(2);
if (options.length && (options.length !== 2 || options[0] !== '--commit' || !/^[a-f0-9]{40}$/.test(options[1]))) {
  throw new Error('Historical source verification requires --commit <full tested SHA>');
}
const root = new URL('../results/stream-cloud/', import.meta.url);
const read = async name => JSON.parse(await readFile(new URL(name, root), 'utf8'));
const receipt = await read('stream-cloud-receipt.json');
const local = await read('stream-runtime-local.json');
const provenance = await read('runtime-provenance.json');
const source = await read('source.json');
const cleanup = await read('cleanup.json');
assert.equal(receipt.runtime, 'cloudflare-real-upstream-worker');
assert.equal(receipt.canonical_claim_verified, false);
assert.equal(receipt.canonical_terminal_acceptance_verified, false);
assert.equal(receipt.result.result.status, 'Success');
assert.equal(receipt.independent_document_value, 70);
assert.equal(receipt.cross_tenant_status, 403);
assert.equal(receipt.invalid_runtime_credential_module_status, 401);
assert.equal(receipt.unsupported_canonical_mode_status, 422);
assert.equal(receipt.printed_streams_returned_redacted, true);
for (const message of ['stream stdout credential=[REDACTED]', 'stream stderr credential=[REDACTED]']) {
  assert.equal(receipt.result.result.logs.filter(row => row.message === message).length, 1);
  assert.equal(receipt.persisted_printed_logs.filter(row => row.message === message).length, 1);
}
assert.equal(receipt.terminal_log_recovery.flushed, 3);
assert.equal(receipt.repeat_log_recovery.flushed, 0);
assert.equal(receipt.runtime_log_stream_drained, true);
assert.equal(receipt.ended_run_status, 401);
assert.equal(receipt.ended_refresh_status, 401);
assert.equal(receipt.result.result.metrics.peak_memory_bytes, null);
assert.equal(local.runtime_probe_passed, true);
assert.equal(local.cloud_invocations, 0);
assert.deepEqual(local.running_after_cleanup, []);
assert.equal(provenance.runtime_sha256, local.packet_sha256['generated-runtime.mjs']);
assert.equal(provenance.runtime_sha256, source.runtime_sha256);
assert.equal(provenance.engine_source, 'f49d4ba99cebd24aa497f1244833b940e640885f');
assert.equal(cleanup.open_version, source.open_version);
assert.equal(cleanup.retired_guard_status, 404);
assert.deepEqual(cleanup.secret_names, []);
assert.equal(cleanup.maximum_invocations, 5);
assert.equal(source.fixture_requests_reserved, 3);
assert.equal(source.total_worker_http_requests, 4);
const reservations = (await readFile(new URL('stream-cloud-invocations.jsonl', root), 'utf8')).trim().split('\n').map(JSON.parse);
assert.deepEqual(reservations.map(row => row.reservation), [1, 2, 3]);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
for (const [path, digest] of Object.entries(source.files)) {
  const bytes = options.length ? (await promisify(execFile)('git', ['show', `${options[1]}:${path}`],
    {cwd: new URL('../../../', import.meta.url), encoding: 'buffer', maxBuffer: 2 * 1024 * 1024})).stdout :
    await readFile(new URL('../../../' + path, import.meta.url));
  assert.equal(hash(bytes), digest, path);
}
console.log('Actual deployed upstream engine/SDK stdout and stderr persisted redacted in PostgreSQL; repeat drain zero; credentials revoked; endpoint closed. Canonical dispatch and physical stop remain unqualified.');
