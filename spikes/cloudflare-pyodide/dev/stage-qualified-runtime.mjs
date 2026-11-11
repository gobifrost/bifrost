// Explicit current qualified artifact. Historical staging keeps its separate pin.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,sep} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {stageRuntimeLoader} from './stage-runtime-loader.mjs';
const PIN='5c80c979a1b3f0c8d6dfe137df0b38e2ea0654f975faaefd6b8011d59191f199';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');

export async function stageQualifiedRuntime(input,output) {
  const bytes=await readFile(input);
  if(hash(bytes)!==PIN)throw new Error('Exact locally qualified SDK runtime artifact required');
  const text=bytes.toString('utf8');
  if(!text.startsWith('export default ')||!text.endsWith(';\n'))throw new Error('Qualified artifact format changed');
  const modules=JSON.parse(text.slice('export default '.length,-2));
  if(Object.keys(modules).length!==472||hash(modules['src/services/execution/engine.py'].py)!=='443bf2b48e0f6ccc8eedf0160d6ac150466e110ba47de1321e31c0c2627709d2')
    throw new Error('Qualified module/engine identity changed');
  // Existing output is never reused or overwritten, including partial attempts.
  await mkdir(output);
  const directory=pathToFileURL(resolve(output)+sep);
  await writeFile(new URL('generated-runtime.mjs',directory),bytes,{flag:'wx'});
  const loaderFiles=await stageRuntimeLoader(directory);
  const provenance={bundle_sha256:PIN,bundle_bytes:bytes.length,module_count:Object.keys(modules).length,
    sdk_source:'a4ba01ca43b25f082af304a9d6473268f393eb52',engine_source:'82c6559fe224a00325510c0a78281f3cc9835213',
    closure_source:'06aeb2c8797c057a5255b4cfab32491754b4f9e4',
    loader_files_sha256:loaderFiles,artifact_bytes_preserved:true,
    scope:'Offline staging of locally qualified code; no new runtime or deployed qualification'};
  await writeFile(new URL('runtime-provenance.json',directory),JSON.stringify(provenance,null,2)+'\n',{flag:'wx'});
  return provenance;
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const [input,output,...extra]=process.argv.slice(2);
  if(!input||!output||extra.length)throw new Error('Supply exact qualified artifact and new task-owned output directory');
  console.log(JSON.stringify(await stageQualifiedRuntime(input,output)));
}
