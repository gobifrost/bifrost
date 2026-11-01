// Exact local API capability surface; no path rewrite or alternate destination.
export function testApiRouteAllowed(url) {
  return url.origin==='https://test-api.invalid' &&
    (url.pathname.startsWith('/api/sdk/') || url.pathname==='/api/tables' ||
      url.pathname.startsWith('/api/tables/') || url.pathname==='/auth/refresh');
}
