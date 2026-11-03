// Actual full artifact assembly/import only. Never starts Python or an account API.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,sep} from 'node:path';
import {pathToFileURL} from 'node:url';
import {stageQualifiedRuntime} from './stage-qualified-runtime.mjs';
const [input,output]=process.argv.slice(2);
if(!input||!output)throw new Error('Exact artifact and new output required');
const provenance=await stageQualifiedRuntime(input,output);
const directory=pathToFileURL(resolve(output)+sep);
const original=await readFile(input),staged=await readFile(new URL('generated-runtime.mjs',directory));
assert.deepEqual(staged,original);
const {runtimeCode}=await import(new URL('runtime-code.mjs',directory));
const {sdkCleanupCode}=await import(new URL('sdk-cleanup-code.mjs',directory));
const code=runtimeCode(null);
assert.equal(code.modules['sdk_cleanup.py'].py,sdkCleanupCode);
assert.ok(code.modules['bifrost/_tls.py']);
assert.ok(code.modules['certifi/cacert.pem'].data instanceof ArrayBuffer);
assert.equal(code.globalOutbound,null);
let refused=false;
try {await stageQualifiedRuntime(input,output);}catch(error){if(error.code!=='EEXIST')throw error;refused=true;}
assert.equal(refused,true);
assert.deepEqual(await readFile(new URL('generated-runtime.mjs',directory)),original);
const report={provenance,constructed_module_count:Object.keys(code.modules).length,
  sdk_cleanup_injected:true,existing_output_refused:refused,
  source_sha256:createHash('sha256').update(staged).digest('hex'),
  staging_sources_sha256:Object.fromEntries(await Promise.all(['stage-qualified-runtime.mjs','stage-runtime-loader.mjs','qualify-staged-runtime.mjs'].map(async name=>
    [name,createHash('sha256').update(await readFile(new URL(name,import.meta.url))).digest('hex')]))),
  node_version:process.version,python_executed:false,cloud_invocations:0,scope:'Full actual qualified artifact assembled/imported in Node only'};
await writeFile(new URL('../assembly-result.json',directory),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(report));
