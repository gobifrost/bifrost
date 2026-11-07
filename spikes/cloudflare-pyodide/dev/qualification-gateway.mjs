// Temporary SDK-only origin bridge. No business scheduling or public UI.
export function allowedSdkPath(path) {
  return path.startsWith('/api/sdk/') || path === '/api/tables' ||
    path.startsWith('/api/tables/') || path === '/auth/refresh';
}
export default {
  async fetch(request, env) {
    if (!env.DEV_AUTH || request.headers.get('X-Qualification-Auth') !== env.DEV_AUTH ||
        !(Date.now() < Number(env.DEV_EXPIRES_AT))) return new Response('Closed', {status: 403});
    const incoming = new URL(request.url);
    if (!allowedSdkPath(incoming.pathname)) return new Response('Denied', {status: 403});
    const origin = new URL(env.API_ORIGIN);
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash)
      return new Response('Invalid origin', {status: 503});
    origin.pathname = incoming.pathname; origin.search = incoming.search;
    return fetch(new Request(origin, request), {redirect: 'manual'});
  },
};
