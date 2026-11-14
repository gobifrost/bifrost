import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,unlink,rmdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {stageRuntimeLoader} from './stage-runtime-loader.mjs';

test('fresh staged loader resolves its SDK cleanup dependency and injects the real adapter',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'bifrost-loader-stage-'));
  const url=pathToFileURL(directory+'/');
  try {
    await writeFile(new URL('generated-runtime.mjs',url),'export default {"sentinel.py":{"py":"value = 7"}};\n');
    const hashes=await stageRuntimeLoader(url);
    const {runtimeCode}=await import(new URL('runtime-code.mjs',url));
    const {sdkCleanupCode}=await import(new URL('sdk-cleanup-code.mjs',url));
    const code=runtimeCode(null);
    assert.equal(code.modules['sentinel.py'].py,'value = 7');
    assert.equal(code.modules['sdk_cleanup.py'].py,sdkCleanupCode);
    assert.match(code.modules['main.py'].py,/await close_sdk\(get_client\(\)\)/);
    for(const name of ['runtime-code.mjs','sdk-cleanup-code.mjs']) {
      const bytes=await readFile(new URL(name,url));
      assert.deepEqual(bytes,await readFile(new URL('../backend/'+name,import.meta.url)));
      assert.equal(createHash('sha256').update(bytes).digest('hex'),hashes[name]);
    }
  } finally {
    for(const name of ['generated-runtime.mjs','runtime-code.mjs','sdk-cleanup-code.mjs']) {
      try {await unlink(new URL(name,url));} catch(error) {if(error.code!=='ENOENT')throw error;}
    }
    await rmdir(directory);
  }
});
