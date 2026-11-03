import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';

test('generated local candidate has valid JS and real Python newlines; original remains unchanged', () => {
  const directory = mkdtempSync(join(tmpdir(), 'canonical-https-'));
  const original = readFileSync(new URL('authorized-local-entry.mjs', import.meta.url));
  const output = join(directory, 'entry.mjs');
  try {
    execFileSync(process.execPath, [new URL('prepare-canonical-https.mjs', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'), output]);
    execFileSync(process.execPath, ['--check', output]);
    const source = readFileSync(output, 'utf8');
    const literal = /code\.modules\['main.py'\]\.py=("[^\r\n]*")\+code/.exec(source)?.[1];
    assert.equal(JSON.parse(literal), 'from httpx_redirect_patch import install\ninstall()\n');
    assert.match(source, /https\.close\(\);httpsPolicies\.delete\(id\)/);
    assert.deepEqual(readFileSync(new URL('authorized-local-entry.mjs', import.meta.url)), original);
    assert.throws(() => execFileSync(process.execPath, [new URL('prepare-canonical-https.mjs', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'), output], {stdio: 'pipe'}));
  } finally {rmSync(directory, {recursive: true, force: true});}
});
