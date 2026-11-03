import assert from 'node:assert/strict';
import {mkdtemp, writeFile, readdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {stageCanonicalCandidate} from './stage-canonical-candidate.mjs';
test('unqualified bundle is refused before any output files are created', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'canonical-stage-'));
  try {
    const artifact = join(directory, 'wrong.mjs');
    await writeFile(artifact, 'export default {};');
    await assert.rejects(stageCanonicalCandidate(artifact, join(directory, 'output')), /Exact qualified runtime/);
    assert.deepEqual(await readdir(directory), ['wrong.mjs']);
  } finally {await rm(directory, {recursive: true, force: true});}
});
