import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/preack-loss/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
assert.match(await read('preack-loss-unit-v1/unit.txt'), /0 != 1 : uncertain started invocation lost native capacity/);
assert.equal((await read('preack-loss-unit-v1/exit-code.txt')).trim(), '1');
assert.match(await read('preack-loss-quality-v2/unit.txt'), /Ran 40 tests[\s\S]*\bOK\b/);
assert.match(await read('preack-loss-quality-v2/quality.txt'), /0 errors, 0 warnings/);
for (const [manifest, before] of [['source.sha256', false], ['before-source.sha256', true]]) {
  for (const line of (await read(manifest)).trim().split(/\r?\n/)) {
    const [digest, path] = line.split(/\s+/);
    const name = path.split('/').at(-1);
    const source = before && name === 'private_http_pool.py'
      ? execFileSync('git', ['show', '50093038cbc4f9ddde61264c453393464a717485:spikes/cloudflare-pyodide/backend/' + name])
      : execFileSync('git', ['show', 'de30250c169f3dc3ce5578bcd6bdd4801cb49611:spikes/cloudflare-pyodide/backend/' + name]);
    assert.equal(hash(source), digest, name);
  }
}
console.log('Fail-first pre-ACK ReadError capacity regression fixed; 40 native unit tests and quality verified. Real HTTP/engine fault and retained-terminal adoption remain unqualified.');
