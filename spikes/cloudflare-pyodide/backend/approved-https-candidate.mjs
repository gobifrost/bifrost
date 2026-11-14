import {createAttemptEgress} from './attempt-egress.mjs';

// Source candidate only: no Worker binding or admission capability enables it.
// Origins and forbidden credentials come from the trusted parent, never the run.
export function createApprovedHttpsCandidate({origins = [], forbiddenCredentials = [],
  maxRequests, deadline, forward, now = Date.now}) {
  if (typeof forward !== 'function' || !Array.isArray(origins) || origins.length > 16 ||
      !Array.isArray(forbiddenCredentials) ||
      forbiddenCredentials.some(value => typeof value !== 'string' || !value.length) ||
      !Number.isInteger(maxRequests) || maxRequests < 1 || maxRequests > 100) {
    throw new TypeError('Invalid trusted HTTPS policy');
  }
  const allowed = new Set();
  for (const value of origins) {
    const url = new URL(value);
    // DNS names only. Operator approval and DNS/network policy remain necessary;
    // this syntactic check is not a DNS rebinding or private-address guarantee.
    if (typeof value !== 'string' || url.protocol !== 'https:' ||
        value !== url.origin || url.username || url.password ||
        !url.hostname.includes('.') || /[\[\]:*]/.test(url.hostname) ||
        /^[\d.]+$/.test(url.hostname) || url.hostname.endsWith('.') ||
        allowed.has(url.origin)) {
      throw new TypeError('Exact approved HTTPS origin required');
    }
    allowed.add(url.origin);
  }
  const credentials = [...forbiddenCredentials];
  let remaining = maxRequests;
  const gate = createAttemptEgress(deadline, request => {
    const url = new URL(request.url);
    if (!allowed.has(url.origin) || url.username || url.password) {
      return Promise.resolve(new Response('Destination denied', {status: 403}));
    }
    // Accidental cleartext forwarding guard, not malicious exfiltration defense.
    for (const value of request.headers.values()) {
      if (credentials.some(credential => value.includes(credential))) {
        return Promise.resolve(new Response('Runtime credential denied', {status: 403}));
      }
    }
    if (!remaining) return Promise.resolve(new Response('Request budget exhausted', {status: 429}));
    const outbound = new Request(request, {redirect: 'manual'});
    remaining--;
    // Errors consume the budget. Never retry an uncertain third-party write.
    return forward(outbound);
  }, now);
  return gate;
}
