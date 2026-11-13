import { test } from 'node:test';
import assert from 'node:assert/strict';
import decideBackend from './eligibility.mjs';

function eligibleAttempt(overrides = {}) {
  return {
    declaration: {
      modules: ['sdk/tables.py'],
      packages: ['httpx', 'pydantic'],
      egressHosts: ['api.example.com'],
      egressRequired: ['api.example.com'],
      estimatedSubrequests: 4,
      cpuBudget: 500,
      source: 'def run(ctx):\n    return ctx.tables.list("t")\n',
      ...overrides.declaration,
    },
    bundleManifest: { modules: ['sdk/tables.py'] },
    subrequestAllowance: 10,
    ...overrides.root,
  };
}

test('eligible short SDK workflow => cloudflare', () => {
  const decision = decideBackend(eligibleAttempt());
  assert.equal(decision.backend, 'cloudflare');
  assert.deepEqual(decision.reasons, []);
});

test('subrequest over budget => linux', () => {
  const decision = decideBackend(
    eligibleAttempt({ declaration: { estimatedSubrequests: 12 }, root: { subrequestAllowance: 10 } }),
  );
  assert.equal(decision.backend, 'linux');
  assert.ok(decision.reasons.some((r) => r.includes('subrequest over budget')));
});

test('unlisted package => linux', () => {
  const decision = decideBackend(
    eligibleAttempt({ declaration: { packages: ['httpx', 'pandas'] } }),
  );
  assert.equal(decision.backend, 'linux');
  assert.ok(decision.reasons.some((r) => r.includes("allowlist miss") && r.includes('pandas')));
});

test('undeclared egress host => linux', () => {
  const decision = decideBackend(
    eligibleAttempt({ declaration: { egressRequired: ['evil.example.com'] } }),
  );
  assert.equal(decision.backend, 'linux');
  assert.ok(decision.reasons.some((r) => r.includes('undeclared egress host')));
});

test('missing declaration => linux fail-closed', () => {
  for (const attempt of [undefined, null, {}, { declaration: null }, { declaration: 'sdk' }]) {
    const decision = decideBackend(attempt);
    assert.equal(decision.backend, 'linux', `expected linux for ${JSON.stringify(attempt)}`);
    assert.ok(
      decision.reasons.length > 0 && decision.reasons.some((r) => r.includes('missing declaration')),
      'fail-closed reason must be explicit',
    );
  }
});

test('process_time usage => linux', () => {
  const decision = decideBackend(
    eligibleAttempt({
      declaration: { source: 'import time\ndef run(ctx):\n    t0 = time.process_time()\n    return t0\n' },
    }),
  );
  assert.equal(decision.backend, 'linux');
  assert.ok(
    decision.reasons.some((r) =>
      r.includes('time.process_time() raises in Pyodide (RuntimeError: processor time ... cannot be represented).') &&
      r.includes('Workflows measuring their own CPU this way must run on Linux.'),
    ),
  );
});

test('clean allowlist hit => cloudflare', () => {
  const decision = decideBackend(
    eligibleAttempt({
      declaration: {
        packages: ['requests', 'msal', 'PyJWT', 'urllib3', 'charset-normalizer'],
      },
    }),
  );
  assert.equal(decision.backend, 'cloudflare');
  assert.deepEqual(decision.reasons, []);
});
