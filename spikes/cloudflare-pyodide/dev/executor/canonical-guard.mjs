// Same guard/expiry contract as the existing isolated dev executor.
export function qualificationStatus(request, env) {
  if (!env.DEV_AUTH || request.headers.get('X-Dev-Auth') !== env.DEV_AUTH
      || !(Date.now() < Number(env.DEV_EXPIRES_AT))) return 403;
  return request.method === 'POST' ? null : 405;
}
