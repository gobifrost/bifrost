import assert from 'node:assert/strict';
import test from 'node:test';
import {deploymentOptions} from './deployment-options.mjs';
const configured = {BIFROST_CF_ACCOUNT_ID: 'a'.repeat(32), BIFROST_CF_EXECUTOR_NAME: 'test-executor',
  BIFROST_CF_API_WORKER: 'test-api', BIFROST_CF_API_ORIGIN: 'https://api.example/'};
test('existing isolated development defaults remain explicit and copied', () => {
  const first = deploymentOptions({});
  assert.equal(first.executorName, 'bifrost-pyodide-spike-ddf316c1');
  first.executorName = 'mutated';
  assert.notEqual(deploymentOptions({}).executorName, first.executorName);
});
test('complete independent installation selects all identities together', () => {
  assert.deepEqual(deploymentOptions(configured), {accountId: 'a'.repeat(32), executorName: 'test-executor',
    apiWorker: 'test-api', apiOrigin: 'https://api.example'});
});
test('partial identity never borrows another installation defaults', () => {
  for (const key of Object.keys(configured)) {
    const value = {...configured}; delete value[key];
    assert.throws(() => deploymentOptions(value), /Complete Cloudflare/);
  }
});
test('invalid identity and non-root or credentialed origins fail closed', () => {
  for (const origin of ['http://api.example', 'https://user:secret@api.example', 'https://api.example/api',
    'https://api.example/?secret=value', 'https://api.example/#fragment']) {
    assert.throws(() => deploymentOptions({...configured, BIFROST_CF_API_ORIGIN: origin}), /Root HTTPS/);
  }
  assert.throws(() => deploymentOptions({...configured, BIFROST_CF_EXECUTOR_NAME: '../other'}), /Invalid Cloudflare/);
});
