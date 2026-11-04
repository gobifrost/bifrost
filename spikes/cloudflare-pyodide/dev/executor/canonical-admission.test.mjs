import assert from 'node:assert/strict';
import test from 'node:test';
import {admitCanonicalFixture} from './canonical-admission.mjs';
for (const requirements of [['subprocess'], ['threads'], ['custom-ca'], ['mtls'], ['persistent-service'],
  ['platform-fetch-https'], ['json', 'subprocess'], ['json', 'json'], null, 'json', Array(33).fill('json')]) {
  test(`unsupported declaration ${JSON.stringify(requirements)} has zero effects`, async () => {
    let effects = 0;
    const response = await admitCanonicalFixture({requirements}, 0, () => {effects++;});
    assert.equal(response.status, 422);
    assert.equal((await response.json()).reason, 'unsupported_requirements');
    assert.equal(effects, 0);
  });
}
test('supported declaration still requires existing fixture provenance', async () => {
  let effects = 0;
  const response = await admitCanonicalFixture({requirements: ['json', 'bifrost-http-sdk']}, 0, () => {effects++;});
  assert.equal(response.status, 422);
  assert.equal(effects, 0);
  assert.equal((await response.json()).reason, 'shape');
});
test('reviewed fixed fixture with SDK-only declaration executes exactly once', async () => {
  const id = '11111111-1111-4111-8111-111111111111';
  const suffix = 'a'.repeat(32), hash = 'b'.repeat(64);
  const path = `workflows/pyodide_qualification_${suffix}.py`;
  const body = {mode: 'canonical', execution_id: id, worker_id: id,
    requirements: ['json', 'async-workflow', 'pinned-workspace', 'bifrost-http-sdk'],
    context: {execution_id: id, file_path: path, function_name: 'qualified', engine_token: 'synthetic',
      timeout_seconds: 30, workflow_deadline: new Date(Date.now()+29000).toISOString(), content_hash: hash},
    manifest: {[path]: hash, [`pyodide_helper_${suffix}.py`]: hash}};
  let effects = 0;
  const response = await admitCanonicalFixture(body, 0, () => {effects++; return Response.json({accepted: true});});
  assert.equal(response.status, 200);
  assert.equal(effects, 1);
});
