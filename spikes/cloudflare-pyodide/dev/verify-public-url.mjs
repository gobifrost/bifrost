import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const options = process.argv.slice(2);
if (options.length && (options.length !== 2 || options[0] !== '--commit' || !/^[a-f0-9]{40}$/.test(options[1]))) {
  throw new Error('Historical source verification requires --commit <full tested SHA>');
}
const receipt = JSON.parse(await readFile(new URL('../results/public-url/receipt.json', import.meta.url), 'utf8'));
const provenance = JSON.parse(await readFile(new URL('../results/public-url/runtime-provenance.json', import.meta.url), 'utf8'));
assert.equal(receipt.runtime_probe_passed, true);
assert.equal(receipt.observation.status, 200);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.observation.body.sdk_api_calls, 0);
assert.equal(receipt.observation.body.genuine_run_authorization_qualified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.state_before_cleanup.OOMKilled, false);
const records = receipt.observation.body.records;
assert.deepEqual(records.map(record => record.mode), ['legacy', 'explicit']);
for (const record of records) {
  assert.equal(record.upstream_result.status, 'Success');
  assert.equal(record.upstream_result.result.transport_url, 'https://test-api.invalid');
  assert.equal(record.upstream_result.result.parent_secret_present, false);
  assert.equal(record.bootstrap.execution_child, true);
  assert.equal(record.upstream_result.metrics.peak_memory_bytes, null);
  assert.equal(record.upstream_result.metrics.cpu_total_seconds, null);
}
assert.equal(records[0].upstream_result.result.public_url, 'https://test-api.invalid');
const expected = 'https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev';
assert.equal(records[1].upstream_result.result.public_url, expected);
assert.equal(records[1].upstream_result.execution_context.public_url, expected);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
assert.equal(provenance.base_runtime_sha256, 'e1cca587e10cb755d49df0f2325c038a361a25f7031142a9e9e896a3c004bd39');
assert.equal(provenance.runtime_sha256, receipt.packet_sha256['generated-runtime.mjs']);
const bootstrap = (await readFile(new URL('../backend/runtime-runner.py', import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
assert.equal(hash(bootstrap), provenance.bootstrap_sha256);
for (const [packet, local] of Object.entries({
  'entry.mjs': './public-url-fixture/entry.mjs',
  'cloudflare.config.ts': './public-url-fixture/cloudflare.config.ts',
  'wrangler.config.ts': './public-url-fixture/wrangler.config.ts',
  'package.json': './public-url-fixture/package.json',
  'python-entry.mjs': './executor/python-entry.mjs', 'policy.mjs': './executor/policy.mjs',
  'runtime-code.mjs': '../backend/runtime-code.mjs',
})) assert.equal(hash(await readFile(new URL(local, import.meta.url))), receipt.packet_sha256[packet], packet);
console.log('Actual Pyodide upstream worker keeps virtual SDK transport and trusted public URL distinct; no cloud/auth claim.');
const cloudRoot = new URL('../results/public-url/', import.meta.url);
const cloud = JSON.parse(await readFile(new URL('cloud-receipt.json', cloudRoot), 'utf8'));
const cleanup = JSON.parse(await readFile(new URL('cloud-cleanup.json', cloudRoot), 'utf8'));
const source = JSON.parse(await readFile(new URL('cloud-source.json', cloudRoot), 'utf8'));
assert.equal(cloud.canonical_claim_verified, false);
assert.equal(cloud.canonical_terminal_acceptance_verified, false);
assert.equal(cloud.trusted_public_url_verified, expected);
assert.equal(cloud.virtual_sdk_transport_preserved, true);
assert.equal(cloud.result.result.status, 'Success');
assert.equal(cloud.result.result.result.value, 70);
assert.equal(cloud.independent_document_value, 70);
assert.equal(cloud.cross_tenant_status, 403);
assert.equal(cloud.ended_run_status, 401);
assert.equal(cloud.ended_refresh_status, 401);
assert.equal(cloud.integration_read_secret_redaction_peer_denial_verified, true);
assert.equal(cleanup.retired_guard_status, 404);
assert.deepEqual(cleanup.secret_names, []);
assert.equal(cleanup.open_version, source.open_version);
assert.equal(source.runtime_sha256, provenance.runtime_sha256);
assert.equal(source.fixture_requests_reserved, 3);
assert.equal(source.total_worker_http_requests, 4);
assert.equal(cleanup.maximum_invocations, 5);
for (const [path, digest] of Object.entries(source.files)) {
  const bytes = options.length ? (await promisify(execFile)('git',
    ['show', `${options[1]}:spikes/cloudflare-pyodide/${path}`],
    {cwd: new URL('../../../', import.meta.url), encoding: 'buffer', maxBuffer: 2 * 1024 * 1024})).stdout :
    await readFile(new URL('../' + path, import.meta.url));
  assert.equal(hash(bytes), digest, path);
}
console.log('Deployed upstream engine/SDK public URL correction, real API operations and closed endpoint receipt verified; canonical dispatch/physical stop remain unqualified.');
