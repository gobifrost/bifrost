// Routing contract for the frontend edge worker. Run with:
//   node --test deploy/cloudflare/frontend/edge.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import worker, {backendRoute, originTarget} from './edge.mjs';

test('public SDK modules remain accessible while internal frame policy routes are blocked', async () => {
  const env = {API_ORIGIN: 'https://api.internal.invalid', ASSETS: {fetch: async () => new Response('module')}};
  assert.equal(await (await worker.fetch(new Request('https://edge.invalid/__bifrost_modules/react.js'), env)).text(), 'module');
  assert.equal((await worker.fetch(new Request('https://edge.invalid/_public_form_frame_policy'), env)).status, 404);
});

test('API, websocket, OAuth and frame-policy routes retain the upstream origin', () => {
  for (const path of ['/api/workflows', '/auth/login', '/ws/notifications', '/mcp', '/mcp/tools',
    '/embed/forms/x', '/embedded/forms/public/key', '/embedded/forms/hmac/id', '/.well-known/oauth-authorization-server', '/token']) {
    assert.equal(backendRoute(path), true, path);
  }
});

test('SPA callbacks and MCP administration are not confused with backend routes', () => {
  for (const path of ['/', '/workflows', '/mcp-servers', '/auth/callback', '/assets/a.js']) assert.equal(backendRoute(path), false, path);
});

test('proxy preserves query and path but never takes its origin from the request', () => {
  assert.equal(originTarget('https://edge.invalid/api/workflows?page=2', 'https://origin.invalid').href,
    'https://origin.invalid/api/workflows?page=2');
  for (const origin of ['http://origin.invalid', 'https://user:password@origin.invalid', 'https://origin.invalid/path']) {
    assert.throws(() => originTarget('https://edge.invalid/', origin), TypeError);
  }
});
