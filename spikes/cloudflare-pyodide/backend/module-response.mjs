// Physical-provider integrity check. Authorization and source selection remain
// upstream; expectedDigest must come from the trusted execution artifact.
export async function verifyModuleResponse(response,path,expectedDigest,{maxBytes=2*1024*1024}={}) {
  // Preserve authentication challenges/errors verbatim; never replay here.
  if(response.status!==200) return response;
  if(!/^[a-f0-9]{64}$/.test(expectedDigest ?? '')) {
    await response.body?.cancel('Unpinned workspace module');
    throw new Error('Unpinned workspace module');
  }
  const reader=response.body?.getReader();
  if(!reader) throw new Error('Workspace module response has no body');
  const chunks=[];
  let size=0;
  try {
    while(true) {
      const {value,done}=await reader.read();
      if(done) break;
      size+=value.byteLength;
      if(size>maxBytes) throw new Error('Workspace module response exceeds limit');
      chunks.push(value);
    }
  } catch(error) {
    await reader.cancel(error);
    throw error;
  } finally { reader.releaseLock(); }
  const bytes=new Uint8Array(size);
  let offset=0;
  for(const chunk of chunks) { bytes.set(chunk,offset);offset+=chunk.byteLength; }
  const module=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  if(module.path!==path || typeof module.content!=='string') throw new Error('Workspace module identity mismatch');
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(module.content))),
    value=>value.toString(16).padStart(2,'0')).join('');
  if(digest!==expectedDigest || module.hash!==digest) throw new Error('Workspace module digest mismatch');
  return new Response(bytes,{status:response.status,statusText:response.statusText,headers:response.headers});
}
