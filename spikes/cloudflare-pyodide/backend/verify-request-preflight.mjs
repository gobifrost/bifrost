import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/request-preflight/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
assert.match(await read('request-preflight-unit-v1/unit.txt'), /Awaited 1 times/);
assert.equal((await read('request-preflight-unit-v1/exit-code.txt')).trim(), '1');
const before = (await read('before-source.sha256')).trim().split(/\r?\n/);
assert.equal(before.length, 2);
for (const line of before) {
  const [digest, path] = line.split(/\s+/);
  const name = path.split('/').at(-1);
  const source = name === 'private_http_pool.py'
    ? execFileSync('git', ['show', '91dc4a6840189d8aa35c9e3c4d3886788e3d728c:spikes/cloudflare-pyodide/backend/' + name])
    : await read('v1-tests.txt');
  assert.equal(hash(source), digest, name);
}
const raw = await read('retained-http-v2/receipt.json');
assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
const report = JSON.parse(raw);
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
for (const name of ['private_http_pool.py', 'http_invocation.py', 'test_private_http_pool.py']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), report.packet_sha256[name], name);
}
assert.equal(report.shared_pool_result.retained_http_preack_loss_verified, true);
assert.equal(report.shared_pool_result.retained_posts, 1);
assert.equal(report.shared_pool_result.failed_replay_unchanged_verified, true);
assert.deepEqual(report.shared_pool_result.native_busy_counts, [2, 2, 1, 0]);
assert.match(await read('request-preflight-quality-v2/unit.txt'), /Ran 54 tests[\s\S]*\bOK\b/);
assert.match(await read('request-preflight-quality-v2/quality.txt'), /0 errors, 0 warnings/);
console.log('Fail-first request preflight fixed with unchanged 256 KiB budget, Unicode/combined bounds and near-limit positive native test. Real ordinary-size engine/SDK recovery passes; maximum-size Pyodide execution remains unqualified.');
