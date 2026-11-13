// Bifrost frontend edge worker: static client assets on Cloudflare,
// backend traffic reverse-proxied through the existing Bifrost frontend gateway.
// API_ORIGIN must serve client/nginx.conf: it supplies /api/auth rewrites and
// embedded-form HTML with the API-resolved framing policy. A bare API is insufficient.
//
// The API keeps full authority (auth, scheduling, data, websockets source).
// This worker only decides, per path, whether to serve a local asset or
// forward to the origin. It never takes the origin from the request.
export function backendRoute(path) {
  if (/^\/auth\/callback(?:\/|$)/.test(path)) return false;
  return /^\/(?:api|auth|ws|mcp|s3|embed|health)(?:\/|$)/.test(path) ||
    /^\/embedded\/forms\/(?:public|hmac)(?:\/|$)/.test(path) ||
    /^\/\.well-known(?:\/|$)/.test(path) ||
    ['/register', '/authorize', '/token', '/openapi.json'].includes(path);
}

export function originTarget(requestUrl, origin) {
  const target = new URL(origin);
  if (target.protocol !== 'https:' || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
    throw new TypeError('Approved HTTPS API origin required');
  }
  const incoming = new URL(requestUrl);
  target.pathname = incoming.pathname;
  target.search = incoming.search;
  return target;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (['/_public_form_frame_policy', '/_hmac_form_frame_policy'].includes(url.pathname)) return new Response('Not found', {status: 404});
    if (backendRoute(url.pathname)) {
      try {
        return await fetch(new Request(originTarget(request.url, env.API_ORIGIN), request), {redirect: 'manual'});
      } catch {
        return new Response('API origin unavailable', {status: 502, headers: {'Cache-Control': 'no-store'}});
      }
    }
    const response = await env.ASSETS.fetch(request);
    if (!response.headers.get('content-type')?.includes('text/html')) return response;
    const headers = new Headers(response.headers);
    headers.set('Cache-Control', 'no-store, must-revalidate');
    headers.delete('ETag');
    return new Response(response.body, {status: response.status, headers});
  },
};
