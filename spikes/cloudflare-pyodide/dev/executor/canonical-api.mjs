import {apiTarget} from './policy.mjs';
import {verifyModuleResponse} from '../../backend/module-response.mjs';
import {verifyResolutionResponse} from '../../backend/resolution-response.mjs';
import {sourceIntegrityResponse} from '../../backend/source-integrity-response.mjs';

// Loopback callbacks may land in another isolate. Authority is immutable props
// plus upstream run credentials, never a lookup into a parent isolate's globals.
export async function canonicalApiFetch(request, props, env, now = Date.now()) {
  if (!Number.isFinite(props?.deadline) || now >= props.deadline)
    return new Response('Attempt capability expired', {status: 410});
  let target;
  try {target = apiTarget(request.url, props.apiOrigin);}
  catch {return new Response('API capability denied', {status: 403});}
  const headers = new Headers(request.headers);
  headers.delete('Host');
  headers.set('X-Qualification-Auth', env.DEV_AUTH);
  const response = await env.DEV_API.fetch(new Request(new Request(target, request), {headers, redirect: 'manual'}));
  const path = new URL(request.url).pathname;
  const observation = {method: request.method, path, status: response.status};
  if (path.startsWith('/api/sdk/modules/')) {
    const module = decodeURIComponent(path.slice('/api/sdk/modules/'.length));
    return sourceIntegrityResponse(() => verifyModuleResponse(response, module, props.manifest[module]), observation);
  }
  if (path === '/api/sdk/modules-resolve')
    return sourceIntegrityResponse(() => verifyResolutionResponse(response, props.manifest), observation);
  return response;
}
