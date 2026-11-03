import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
test('overflow fixture requires completed delivery failure and preserves shared source', () => {
  const directory = mkdtempSync(join(tmpdir(), 'rpc-overflow-'));
  const input = fileURLToPath(new URL('shared-native-pool.py', import.meta.url));
  const script = fileURLToPath(new URL('prepare-rpc-overflow-fixture.mjs', import.meta.url));
  const output = join(directory, 'fixture.py'), original = readFileSync(input);
  try {
    execFileSync(process.execPath, [script, input, output]);
    const source = readFileSync(output, 'utf8');
    assert.match(source, /5000000 if oversized_body/);
    assert.match(source, /ExecutorRpcResultTooLargeError/);
    assert.match(source, /rpc_wire_budget_failure_verified/);
    assert.match(source, /outcome\.get\('engine_outcome_delivered'\) is not False/);
    assert.deepEqual(readFileSync(input), original);
    assert.throws(() => execFileSync(process.execPath, [script, input, output], {stdio: 'pipe'}));
  } finally {rmSync(directory, {recursive: true, force: true});}
});
