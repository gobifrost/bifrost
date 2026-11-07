import {test} from 'node:test';
import assert from 'node:assert/strict';
import gateway, {allowedSdkPath} from './qualification-gateway.mjs';
test('SDK bridge rejects missing, expired and unrelated capabilities before network', async () => {
  const env = {DEV_AUTH: 'synthetic', DEV_EXPIRES_AT: String(Date.now()+60000), API_ORIGIN: 'https://origin.invalid'};
  assert.equal((await gateway.fetch(new Request('https://gateway.invalid/api/sdk/config/get'), env)).status, 403);
  const request = new Request('https://gateway.invalid/api/workflows', {headers: {'X-Qualification-Auth': 'synthetic'}});
  assert.equal((await gateway.fetch(request, env)).status, 403);
  assert.equal((await gateway.fetch(request, {...env, DEV_EXPIRES_AT: '0'})).status, 403);
  assert.equal(allowedSdkPath('/api/sdk/modules/x.py'), true);
  assert.equal(allowedSdkPath('/api/tables/x'), true);
  assert.equal(allowedSdkPath('/api/sdk-other'), false);
});
test('SDK bridge preserves run authorization and never follows an origin redirect', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async (request, options) => {
    assert.equal(request.url, 'https://origin.invalid/api/sdk/config/get?q=1');
    assert.equal(request.headers.get('Authorization'), 'Bearer synthetic-run');
    assert.equal(options.redirect, 'manual');
    return new Response(null, {status: 302});
  };
  try {
    const response = await gateway.fetch(new Request('https://gateway.invalid/api/sdk/config/get?q=1', {
      headers: {'X-Qualification-Auth': 'synthetic', Authorization: 'Bearer synthetic-run'},
    }), {DEV_AUTH: 'synthetic', DEV_EXPIRES_AT: String(Date.now()+60000), API_ORIGIN: 'https://origin.invalid'});
    assert.equal(response.status, 302);
  } finally {globalThis.fetch = previous;}
});
