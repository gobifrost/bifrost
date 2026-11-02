import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {mkdtemp, writeFile, unlink, rmdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';
import test from 'node:test';

test('an unpinned base artifact is rejected before derived runtime staging', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'bifrost-runtime-integrity-'));
  const input = join(directory, 'invalid-runtime.mjs');
  try {
    await writeFile(input, 'export default {};\n', {flag: 'wx'});
    await assert.rejects(promisify(execFile)(process.execPath,
      [fileURLToPath(new URL('./stage_runtime.mjs', import.meta.url)), input]), error => {
        assert.match(error.stderr, /Prepared runtime artifact integrity mismatch/);
        return true;
      });
  } finally {
    await unlink(input);
    await rmdir(directory);
  }
});
