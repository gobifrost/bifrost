import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';

const base = new URL('../results/delivery-lock/', import.meta.url);
const read = path => readFile(new URL(path, base), 'utf8');
assert.match(await read('delivery-lock-v1/before.txt'), /another route dispatch waits for stalled ACK/);
assert.match(await read('delivery-lock-v1/before.txt'), /FAILED \(failures=1\)/);
assert.match(await read('delivery-lock-v1/after.txt'), /Ran 1 test[\s\S]*\bOK\b/);
assert.match(await read('delivery-lock-v1/quality.txt'), /Read-only file system/);
assert.match(await read('delivery-lock-v2/tests.txt'), /Ran 2 tests[\s\S]*\bOK\b/);
assert.match(await read('delivery-lock-v2/owner-tests.txt'), /Ran 6 tests[\s\S]*\bOK\b/);
assert.match(await read('delivery-lock-v2/quality.txt'), /0 errors, 0 warnings/);
for (const line of (await read('delivery-lock-v2/source.sha256')).trim().split('\n')) {
  const [digest, path] = line.split(/\s+/);
  const name = path.split('/').at(-1);
  const source = execFileSync('git', ['show', '8fc440978624c898d81441127cb51b2b73a805ce:spikes/cloudflare-pyodide/backend/' + name]);
  assert.equal(createHash('sha256').update(source).digest('hex'), digest, name);
}
console.log('Fail-first receipt concurrency regression, two changed-source tests and six existing owner guards verified. Native unit evidence only.');
