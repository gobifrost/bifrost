import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,stat,unlink,rmdir,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {stageQualifiedRuntime} from './stage-qualified-runtime.mjs';

test('unqualified artifact is rejected before any output directory is created',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'bifrost-qualified-stage-'));
  const input=join(directory,'input.mjs'),output=join(directory,'output');
  try {
    await writeFile(input,'export default {};\n');
    await assert.rejects(stageQualifiedRuntime(input,output),/Exact locally qualified SDK runtime artifact required/);
    await assert.rejects(stat(output),{code:'ENOENT'});
  } finally {await unlink(input);await rmdir(directory);}
});

test('rejected artifact cannot alter an existing output or provenance',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'bifrost-qualified-stage-'));
  const input=join(directory,'input.mjs'),output=join(directory,'output'),sentinel=join(output,'runtime-provenance.json');
  try {
    await writeFile(input,'export default {};\n');await mkdir(output);await writeFile(sentinel,'retained original');
    await assert.rejects(stageQualifiedRuntime(input,output),/Exact locally qualified SDK runtime artifact required/);
    assert.equal(await readFile(sentinel,'utf8'),'retained original');
  } finally {await unlink(sentinel);await rmdir(output);await unlink(input);await rmdir(directory);}
});
