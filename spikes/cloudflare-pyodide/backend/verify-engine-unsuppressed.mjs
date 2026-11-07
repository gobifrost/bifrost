import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const receipt = JSON.parse(read('engine-unsuppressed-v6.json'));
const diagnostic = JSON.parse(read('engine-unsuppressed-v6-diagnostic.json'));
assert.equal(receipt.qualification_passed, false);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(diagnostic.collection_error_type, 'ConnectionError');
assert.equal(diagnostic.canonical_status, 'Timeout');
assert.equal(diagnostic.access_status, 401);
assert.equal(diagnostic.native_busy_count, 1);
assert.equal(diagnostic.retirement_refused, true);
assert.equal(diagnostic.physical_stopped, false);
assert.equal(diagnostic.vendor_after_release.committed, true);
const directory = mkdtempSync(join(tmpdir(), 'verify-unsuppressed-'));
try {
  const output = join(directory, 'fixture.py');
  execFileSync(process.execPath, [fileURLToPath(new URL('prepare-engine-cancel-fixture.mjs', import.meta.url)),
    fileURLToPath(new URL('shared-native-pool.py', import.meta.url)), output, '--unsuppressed']);
  assert.equal(createHash('sha256').update(readFileSync(output)).digest('hex'), receipt.packet_sha256['shared-native-pool.py']);
} finally {rmSync(directory, {recursive: true, force: true});}
console.log('Unsuppressed failure/deadline/retirement-refusal evidence verified; qualification remains incomplete.');
