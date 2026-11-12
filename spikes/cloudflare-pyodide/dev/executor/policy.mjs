export function apiTarget(url, origin) {
  const incoming = new URL(url);
  if (incoming.origin !== 'https://test-api.invalid' ||
      !(incoming.pathname.startsWith('/api/sdk/') || incoming.pathname === '/api/tables' ||
        incoming.pathname.startsWith('/api/tables/') || incoming.pathname === '/auth/refresh' ||
        incoming.pathname === '/api/agent-runs/enqueue' ||
        incoming.pathname.startsWith('/api/agent-runs/'))) {
    throw new TypeError('API capability denied');
  }
  const target = new URL(origin);
  if (target.protocol !== 'https:' || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
    throw new TypeError('Fixed HTTPS API origin required');
  }
  target.pathname = incoming.pathname;
  target.search = incoming.search;
  return target;
}

export function attemptPayload(body, publicUrl) {
  const url = new URL(publicUrl);
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new TypeError('Trusted root HTTPS public URL required');
  }
  return {action: 'execute', execution_id: body.execution_id, context: body.context,
    api_url: 'https://test-api.invalid', public_url: url.origin};
}
