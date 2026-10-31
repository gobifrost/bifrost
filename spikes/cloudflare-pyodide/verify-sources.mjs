import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const manifest=JSON.parse(await readFile(new URL('./results/source-manifest.json',import.meta.url),'utf8'));
for(const [file,expected] of Object.entries(manifest.files)) {
  for(const base of ['../../api/','./src/vendor/']) {
    const bytes=await readFile(new URL(base+file,import.meta.url));
    assert.equal(createHash('sha256').update(bytes).digest('hex'),expected,base+file);
  }
}
console.log(`Verified ${Object.keys(manifest.files).length} exact upstream source copies against recorded SHA256.`);
