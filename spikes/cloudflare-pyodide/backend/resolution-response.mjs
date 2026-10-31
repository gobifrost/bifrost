import {boundedText} from '../probe-io.mjs';
import {verifyModuleResponse} from './module-response.mjs';

// Admission integrity only; upstream still authenticates and resolves modules.
export async function verifyResolutionResponse(response,manifest) {
  if(response.status!==200) return response;
  const text=await boundedText(response);
  const value=JSON.parse(text);
  if(value.kind==='module' || value.kind==='package') {
    // Initial qualification supports loose workspace paths only. Do not silently
    // flatten Solution paths or substitute their authorization semantics.
    if(value.storage_path && value.storage_path!==value.path) throw new Error('Scoped resolution not admitted');
    const checked=await verifyModuleResponse(Response.json(value),value.path,manifest[value.path]);
    await checked.body?.cancel();
  } else if(value.kind==='namespace') {
    if(!Object.keys(manifest).some(path=>path.startsWith(value.path+'/'))) throw new Error('Unpinned namespace');
  } else if(value.kind!=='not_found') throw new Error('Invalid resolution kind');
  return new Response(text,{status:response.status,headers:response.headers});
}
