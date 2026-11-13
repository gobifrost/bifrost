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

test('session refresh reaches the configured gateway with credentials and returns its rotated cookie', async (t) => {
  const body = JSON.stringify({refresh_token: 'test-refresh-token'});
  const upstream = new Response('{"access_token":"test-access-token"}', {
    headers: {'Content-Type': 'application/json', 'Set-Cookie': 'refresh=replacement; Secure; HttpOnly'},
  });
  t.mock.method(globalThis, 'fetch', async (request, options) => {
    // The existing nginx gateway, not this Worker, rewrites /api/auth to /auth.
    assert.equal(request.url, 'https://gateway.invalid/api/auth/refresh');
    assert.equal(request.method, 'POST');
    assert.equal(request.headers.get('cookie'), 'refresh=previous');
    assert.equal(request.headers.get('content-type'), 'application/json');
    assert.equal(await request.text(), body);
    assert.equal(options.redirect, 'manual');
    return upstream;
  });
  const response = await worker.fetch(new Request('https://edge.invalid/api/auth/refresh', {
    method: 'POST', headers: {'Cookie': 'refresh=previous', 'Content-Type': 'application/json'}, body,
  }), {API_ORIGIN: 'https://gateway.invalid'});
  assert.equal(response, upstream);
  assert.equal(response.headers.get('set-cookie'), 'refresh=replacement; Secure; HttpOnly');
});

test('embedded form documents retain gateway HTML and framing restrictions, including missing forms', async (t) => {
  const paths = ['/embedded/forms/public/test-key', '/embedded/forms/hmac/00000000-0000-0000-0000-000000000001'];
  for (const path of paths) {
    const upstream = new Response('<html>gateway form</html>', {
      headers: {'Content-Type': 'text/html', 'Content-Security-Policy': 'frame-ancestors https://parent.invalid',
        'Cache-Control': 'no-store, must-revalidate'},
    });
    const mock = t.mock.method(globalThis, 'fetch', async (request) => {
      assert.equal(request.url, `https://gateway.invalid${path}?token=test-session`);
      return upstream;
    });
    const response = await worker.fetch(new Request(`https://edge.invalid${path}?token=test-session`), {
      API_ORIGIN: 'https://gateway.invalid',
      ASSETS: {fetch: () => assert.fail('embedded forms must use the gateway document')},
    });
    assert.equal(response, upstream);
    assert.equal(response.headers.get('content-security-policy'), 'frame-ancestors https://parent.invalid');
    assert.equal(await response.text(), '<html>gateway form</html>');
    mock.mock.restore();
  }
  t.mock.method(globalThis, 'fetch', async () => new Response('Not found', {status: 404}));
  const missing = await worker.fetch(new Request('https://edge.invalid/embedded/forms/public/missing'), {
    API_ORIGIN: 'https://gateway.invalid',
  });
  assert.equal(missing.status, 404);
});
