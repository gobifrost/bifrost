export function apiTarget(url, origin) {
  const incoming = new URL(url);
  if (incoming.origin !== 'https://test-api.invalid' ||
      !(incoming.pathname.startsWith('/api/sdk/') || incoming.pathname === '/api/tables' ||
        incoming.pathname.startsWith('/api/tables/') || incoming.pathname === '/auth/refresh')) {
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
