import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decideGatewayAction, sanitizedChildContext, injectionHeaders, externalHeaders,
  isTokenShaped, containsTokenShaped, DUMMY_CREDENTIAL} from './gateway-injection.mjs';

test('test-api.invalid injects, declared externals forward without auth, unlisted rejects', () => {
  assert.equal(decideGatewayAction('https://test-api.invalid/api/sdk/config/get', undefined).action, 'inject-test-api');
  assert.equal(decideGatewayAction('https://test-api.invalid/api/sdk/modules/a.py', {hosts: []}).action, 'inject-test-api');
  assert.equal(decideGatewayAction('https://example.com/data', {hosts: ['example.com']}).action, 'forward-external');
  const bad = decideGatewayAction('https://evil.example/data', {hosts: ['example.com']});
  assert.equal(bad.action, 'reject');
  assert.ok(['egress', 'hosts', 'hostname', 'unlisted'].includes(bad.reason));
  const invalidShape = decideGatewayAction('https://evil.example/data', {hosts: 'not-an-array'});
  assert.equal(invalidShape.action, 'reject');
  assert.equal(invalidShape.reason, 'hosts');
});

test('non-https, credentials, ports and unparsable URLs reject before any effect', () => {
  assert.equal(decideGatewayAction('http://test-api.invalid/api/sdk/config/get', undefined).action, 'reject');
  assert.equal(decideGatewayAction('https://user:pass@test-api.invalid/api/sdk/config/get', undefined).action, 'reject');
  assert.equal(decideGatewayAction('https://test-api.invalid:8443/api/sdk/config/get', undefined).action, 'reject');
  assert.equal(decideGatewayAction('not-a-url', undefined).action, 'reject');
});

test('child context sanitization removes real tokens without being token-shaped', () => {
  const real = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c';
  const out = sanitizedChildContext({engine_token: real, engine_refresh_token: real, other: 'keep'});
  assert.equal(out.engine_token, DUMMY_CREDENTIAL);
  assert.equal(out.engine_refresh_token, DUMMY_CREDENTIAL);
  assert.equal(out.other, 'keep');
  assert.equal(isTokenShaped(DUMMY_CREDENTIAL), false);
  assert.equal(isTokenShaped(real), true);
});

test('token-shape detector ignores identities and digests, flags JWT and b64 secrets', () => {
  assert.equal(isTokenShaped('gateway-injected'), false);
  assert.equal(isTokenShaped('synthetic-invalid-runtime-token'), false);
  assert.equal(isTokenShaped('123e4567-e89b-12d3-a456-426614174000'), false);
  assert.equal(isTokenShaped('443bf2b48e0f6ccc8eedf0160d6ac150466e2ea'), false);
  assert.equal(isTokenShaped('eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c'), true);
  // 32-byte DEV_AUTH-style base64
  assert.equal(isTokenShaped('MDEyMzQ1Njc4OUFCQ0RFRjAxMjM0NTY3ODlBQkNERUYwMTI9'), true);
  assert.equal(containsTokenShaped('Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c'), true);
  assert.equal(containsTokenShaped('gateway-injected'), false);
});

test('injection headers carry run token + dev auth, strip incoming auth and cookies', () => {
  const incoming = new Headers({Authorization: 'Bearer ' + DUMMY_CREDENTIAL,
    Cookie: 'session=abc', 'X-Dev-Auth': 'evil', 'X-Qualification-Auth': 'evil', Host: 'test-api.invalid'});
  const out = injectionHeaders(incoming, 'REAL-RUN-TOKEN', 'DEV-AUTH');
  assert.equal(out.get('Authorization'), 'Bearer REAL-RUN-TOKEN');
  assert.equal(out.get('X-Qualification-Auth'), 'DEV-AUTH');
  assert.equal(out.get('Cookie'), null);
  assert.equal(out.get('X-Dev-Auth'), null);
  assert.equal(out.get('Host'), null);
});

test('external headers never forward auth, cookies or qualification secrets', () => {
  const incoming = new Headers({Authorization: 'Bearer REAL', Cookie: 's=1',
    'X-Dev-Auth': 'd', 'X-Qualification-Auth': 'q'});
  const out = externalHeaders(incoming);
  assert.equal(out.get('Authorization'), null);
  assert.equal(out.get('Cookie'), null);
  assert.equal(out.get('X-Dev-Auth'), null);
  assert.equal(out.get('X-Qualification-Auth'), null);
});
