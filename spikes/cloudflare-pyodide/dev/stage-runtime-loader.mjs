import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

export async function stageRuntimeLoader(directory) {
  const hashes={};
  for(const name of ['runtime-code.mjs','sdk-cleanup-code.mjs']) {
    const bytes=await readFile(new URL('../backend/'+name,import.meta.url));
    await writeFile(new URL(name,directory),bytes);
    hashes[name]=createHash('sha256').update(bytes).digest('hex');
  }
  return hashes;
}
