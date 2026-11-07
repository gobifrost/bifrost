import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import test from 'node:test';

test('staging preserves exact Python source and refuses overwrite', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'cooperative-code-'));
  const output = join(directory, 'cooperative-code.mjs');
  const generator = fileURLToPath(new URL('stage-cooperative-code.mjs', import.meta.url));
  const source = readFileSync(new URL('cooperative_execution.py', import.meta.url), 'utf8');
  try {
    execFileSync(process.execPath, [generator, output]);
    const generated = await import(pathToFileURL(output).href);
    assert.equal(generated.default, source);
    const before = readFileSync(output);
    assert.throws(() => execFileSync(process.execPath, [generator, output], {stdio: 'pipe'}));
    assert.deepEqual(readFileSync(output), before);
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
});
