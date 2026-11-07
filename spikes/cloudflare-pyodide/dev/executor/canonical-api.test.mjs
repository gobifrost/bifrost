import {test} from 'node:test';
import assert from 'node:assert/strict';
import {canonicalApiFetch} from './canonical-api.mjs';
test('callback works with serialized props and no parent-isolate state', async () => {
  const props = JSON.parse(JSON.stringify({apiOrigin: 'https://origin.invalid', deadline: 2000, manifest: {}}));
  let calls = 0;
  const env = {DEV_AUTH: 'synthetic', DEV_API: {fetch: async request => {
    calls++;
    assert.equal(request.url, 'https://origin.invalid/api/sdk/config/get');
    assert.equal(request.headers.get('Authorization'), 'Bearer scoped-run');
    assert.equal(request.headers.get('X-Qualification-Auth'), 'synthetic');
    assert.equal(request.redirect, 'manual');
    return new Response('{}');
  }}};
  const request = () => new Request('https://test-api.invalid/api/sdk/config/get', {headers: {Authorization: 'Bearer scoped-run'}});
  assert.equal((await canonicalApiFetch(request(), props, env, 1000)).status, 200);
  assert.equal((await canonicalApiFetch(request(), props, env, 2000)).status, 410);
  assert.equal(calls, 1);
  assert.equal((await canonicalApiFetch(new Request('https://evil.invalid/api/sdk/config/get'), props, env, 1000)).status, 403);
  assert.equal(calls, 1);
});
test('serialized workspace manifest still rejects changed module bytes', async () => {
  const props = {apiOrigin: 'https://origin.invalid', deadline: 2000, manifest: {'workflow.py': '0'.repeat(64)}};
  const response = await canonicalApiFetch(new Request('https://test-api.invalid/api/sdk/modules/workflow.py'), props,
    {DEV_AUTH: 'synthetic', DEV_API: {fetch: async () => Response.json({path: 'workflow.py', content: 'changed', hash: '0'.repeat(64)})}}, 1000);
  assert.equal(response.status, 409);
});
