import assert from 'node:assert/strict';
import test from 'node:test';
import {boundHttpsFixturePolicy} from './fixture-https-policy.mjs';
const org = '11111111-1111-4111-8111-111111111111';
const input = () => ({execution_id: 'attempt', context: {organization: {id: org}},
  https_policy: {execution_id: 'attempt', org_id: org, policy_id: 'synthetic-fails-first',
    origins: ['https://vendor.example'], max_requests: 6}});
test('fixed receiver snapshots matching attempt and scope', () => {
  const body = input(), policy = boundHttpsFixturePolicy(body);
  body.https_policy.origins.push('https://attacker.example');
  assert.deepEqual(policy.origins, ['https://vendor.example']);
  assert.ok(Object.isFrozen(policy)); assert.ok(Object.isFrozen(policy.origins));
});
test('receiver refuses missing or changed policy before runtime capability creation', () => {
  for (const change of [body => {delete body.https_policy;},
    body => {body.https_policy.org_id = '22222222-2222-4222-8222-222222222222';},
    body => {body.https_policy.execution_id = 'other';},
    body => {body.https_policy.policy_id = 'unapproved';},
    body => {body.https_policy.origins[0] = 'https://other.example';},
    body => {body.https_policy.max_requests = 7;},
    body => {body.https_policy.extra = true;}]) {
    const body = input(); change(body); assert.equal(boundHttpsFixturePolicy(body), null);
  }
});
