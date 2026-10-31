// Diagnostic transport only. Evidence remains untrusted JSON, never executable input.
export const CLOUD_PROBE_URL='https://bifrost-pyodide-spike-ddf316c1.midtown-technology-group.workers.dev';

export function cloudProbeFetch(secret,init={},path='') {
  if(path!=='' && !/^\/[a-z_]{1,64}$/.test(path)) throw new TypeError('Invalid fixed-probe path');
  if(secret!==undefined && !/^[A-Za-z0-9+/]{43}=$/.test(secret)) throw new TypeError('Invalid temporary probe credential');
  const headers=new Headers(init.headers);
  if(secret!==undefined) headers.set('X-Spike-Auth',secret);
  // Redirects must never forward the custom authentication header elsewhere.
  return fetch(CLOUD_PROBE_URL+path,{...init,headers,redirect:'error',signal:init.signal ?? AbortSignal.timeout(30000)});
}

export async function boundedText(response,maxBytes=1024*1024) {
  const reader=response.body?.getReader();
  if(!reader) return '';
  const chunks=[];
  let size=0;
  try {
    while(true) {
      const {value,done}=await reader.read();
      if(done) break;
      size+=value.byteLength;
      if(size>maxBytes) throw new Error('Diagnostic response exceeds byte limit');
      chunks.push(value);
    }
  } catch(error) {
    await reader.cancel(error);
    throw error;
  } finally { reader.releaseLock(); }
  const bytes=new Uint8Array(size);
  let offset=0;
  for(const chunk of chunks) { bytes.set(chunk,offset);offset+=chunk.byteLength; }
  return new TextDecoder('utf-8',{fatal:true}).decode(bytes);
}

export async function boundedJson(response,maxBytes) {
  return JSON.parse(await boundedText(response,maxBytes));
}
