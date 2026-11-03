// Explicit current qualified artifact. Historical staging keeps its separate pin.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,sep} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {stageRuntimeLoader} from './stage-runtime-loader.mjs';
const PIN='61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');

export async function stageQualifiedRuntime(input,output) {
  const bytes=await readFile(input);
  if(hash(bytes)!==PIN)throw new Error('Exact locally qualified SDK runtime artifact required');
  const text=bytes.toString('utf8');
  if(!text.startsWith('export default ')||!text.endsWith(';\n'))throw new Error('Qualified artifact format changed');
  const modules=JSON.parse(text.slice('export default '.length,-2));
  if(Object.keys(modules).length!==466||hash(modules['src/services/execution/engine.py'].py)!=='6690efa6fc513a0f457c30cfed59da091bf3ae292d186bc72cd436c871759c21')
    throw new Error('Qualified module/engine identity changed');
  // Existing output is never reused or overwritten, including partial attempts.
  await mkdir(output);
  const directory=pathToFileURL(resolve(output)+sep);
  await writeFile(new URL('generated-runtime.mjs',directory),bytes,{flag:'wx'});
  const loaderFiles=await stageRuntimeLoader(directory);
  const provenance={bundle_sha256:PIN,bundle_bytes:bytes.length,module_count:Object.keys(modules).length,
    sdk_source:'139596497d57b219ddcc3616aa66ec58e1b3cf3c',engine_source:'f49d4ba99cebd24aa497f1244833b940e640885f',
    closure_source:'785b560b9973020e1c13401383e1a73a87a54940',
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
