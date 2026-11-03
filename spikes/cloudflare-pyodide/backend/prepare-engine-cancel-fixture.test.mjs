import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

test('engine cancel fixture preserves owner fencing and labels deliberate ACK hold', () => {
  const directory = mkdtempSync(join(tmpdir(), 'engine-cancel-'));
  const input = fileURLToPath(new URL('shared-native-pool.py', import.meta.url));
  const script = fileURLToPath(new URL('prepare-engine-cancel-fixture.mjs', import.meta.url));
  const output = join(directory, 'fixture.py'), original = readFileSync(input);
  try {
    execFileSync(process.execPath, [script, input, output]);
    const source = readFileSync(output, 'utf8');
    assert.match(source, /except asyncio\.CancelledError:/);
    assert.match(source, /engine_cooperative_cancel_suppressed/);
    assert.match(source, /__fixture_cancel/);
    assert.match(source, /acknowledgement = await revoker\.revoke\(ref\.lease, run\)/);
    assert.match(source, /consumer_ack_deliberately_held/);
    assert.match(source, /Physical cancellation is not qualified for this runtime/);
    assert.deepEqual(readFileSync(input), original);
    assert.throws(() => execFileSync(process.execPath, [script, input, output], {stdio: 'pipe'}));
  } finally {rmSync(directory, {recursive: true, force: true});}
});

test('unsuppressed diagnostic demands failed collection and real deadline before reporting uncertainty', () => {
  const directory = mkdtempSync(join(tmpdir(), 'engine-cancel-unsuppressed-'));
  const input = fileURLToPath(new URL('shared-native-pool.py', import.meta.url));
  const script = fileURLToPath(new URL('prepare-engine-cancel-fixture.mjs', import.meta.url));
  const output = join(directory, 'fixture.py');
  try {
    execFileSync(process.execPath, [script, input, output, '--unsuppressed']);
    const source = readFileSync(output, 'utf8');
    assert.doesNotMatch(source, /engine_cooperative_cancel_suppressed/);
    assert.match(source, /Unsuppressed cancellation fabricated an engine result/);
    assert.match(source, /current\.status == ExecutionStatus\.TIMEOUT/);
    assert.match(source, /after_timeout\.status_code != 401/);
    assert.match(source, /Unsuppressed cancellation retains uncertain native obligation/);
  } finally {rmSync(directory, {recursive: true, force: true});}
});
