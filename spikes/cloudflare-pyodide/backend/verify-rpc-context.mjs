import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root = new URL('../results/rpc-context/', import.meta.url);
for (const v of [1, 2]) {
  const read = suffix => readFileSync(new URL(`v${v}${suffix}`, root));
  const receipt = JSON.parse(read('.json'));
  assert.equal(receipt.cloud_invocations, 0);
  assert.equal(receipt.engine_sdk_execution_verified, false);
  assert.equal(receipt.running_after_cleanup, 'false');
  assert.equal('observation' in receipt, false);
  assert.equal(createHash('sha256').update(read('-entry.mjs.txt')).digest('hex'),
    receipt.packet_sha256['project/backend/rpc-context-entry.mjs']);
  assert.match(read('-log.txt').toString(), v === 1
    ? /Entrypoints to dynamically-loaded workers cannot be transferred/
    : /Cannot perform I\/O on behalf of a different request/);
}
console.log('Two exact local boundary failures preserved; no cancellation or cloud acceptance.');
