import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {parseLosslessJson} from './lossless-json.mjs';
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const receipt = parseLosslessJson(read('cooperative-numeric-v6.json'));
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.shared_pool_result.exact_integer_roundtrip_verified, true);
assert.equal(receipt.shared_pool_result.terminal_obligations_released, true);
assert.equal(receipt.packet_sha256['project/backend/cooperative-rpc-result.mjs'],
  createHash('sha256').update(execFileSync('git', ['show',
    'c7b0c4ae4:spikes/cloudflare-pyodide/backend/cooperative-rpc-result.mjs'])).digest('hex'));
const success = Object.values(receipt.shared_pool_result.attempts).find(item => item.canonical_status === 'Success');
assert.ok(success);
const result = JSON.stringify(success.canonical_result);
assert.match(result, /9007199254740993/);
assert.equal(success.evidence.runtime_cleanup.sdk_close_returned, true);
assert.equal(success.access_after_terminal_status, 401);
console.log('Actual canonical engine RPC integer fidelity verified; marshaling memory/size remain separate.');
