import assert from 'node:assert/strict';
import test from 'node:test';
import {qualificationStatus} from './canonical-guard.mjs';
test('canonical candidate stays closed without configured matching unexpired guard', () => {
  const request = new Request('https://fixture.invalid/', {method: 'POST', headers: {'X-Dev-Auth': 'synthetic'}});
  for (const env of [{}, {DEV_AUTH: 'other', DEV_EXPIRES_AT: String(Date.now()+60000)},
    {DEV_AUTH: 'synthetic', DEV_EXPIRES_AT: '0'}, {DEV_AUTH: 'synthetic', DEV_EXPIRES_AT: 'invalid'}]) {
    assert.equal(qualificationStatus(request, env), 403);
  }
});
test('valid guard admits only POST; authentication precedes method disclosure', () => {
  const env = {DEV_AUTH: 'synthetic', DEV_EXPIRES_AT: String(Date.now()+60000)};
  const request = method => new Request('https://fixture.invalid/', {method, headers: {'X-Dev-Auth': 'synthetic'}});
  assert.equal(qualificationStatus(request('POST'), env), null);
  assert.equal(qualificationStatus(request('GET'), env), 405);
  assert.equal(qualificationStatus(new Request('https://fixture.invalid/'), env), 403);
});
