// Gateway injection decision (Cloudflare golden path): pure, testable.
// Child NEVER holds credentials; parent gateway injects run-scoped auth.
//
// Contract (follows https://developers.cloudflare.com/dynamic-workers/usage/egress-control/):
// - Allowlist check FIRST via egress-allowlist logic (test-api.invalid + declared hosts only).
//   Violation -> 422 egress-rejected, zero calls, only shape class echoed.
// - test-api.invalid -> inject `Authorization: Bearer <run token held ONLY in parent env/props>`
//   + forward to real API origin; strip X-Dev-Auth/X-Qualification-Auth/Cookie always.
// - Declared externals -> forward WITHOUT any auth headers; strip always.
// - Child env carries NO tokens/secrets (verified by dumping child env in probe).
// - SDK keeps working: child builds requests with dummy/absent local credential
//   tolerated because gateway injects real one. No upstream api/ SDK changes;
//   spike-side adapters only (runtime-runner, python-entry, executor entry).
//
// Smallest spike-side shim (tech debt): DUMMY_CREDENTIAL satisfies
// `run_execution`'s "Missing pre-minted runtime credential" presence check
// without being token-shaped. Real refresh via gateway is not yet implemented;
// bounded qualification runs within token lifetime so no refresh occurs.
import {egressRejection, isUrlAllowed, VIRTUAL_TEST_HOST} from './egress-allowlist.mjs';

export const DUMMY_CREDENTIAL = 'gateway-injected';

// JWT = three dot-separated base64url parts (real run tokens are JWTs).
// Dummy, UUIDs (execution/workflow IDs), SHA256 hex digests and
// synthetic-invalid-runtime-token carry no dots in this shape and are ignored.
const JWT_RE = /[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/;
// 40+ char base64 secret (DEV_AUTH-style 32 bytes -> 44 chars, may end with =).
// Requires +/= to avoid flagging hex digests or UUIDs.
const B64_SECRET_RE = /[A-Za-z0-9+/]{38,}[A-Za-z0-9+/=]{2,}/;

export function isTokenShaped(value) {
  if (typeof value !== 'string' || value.length < 10) return false;
  if (value === DUMMY_CREDENTIAL) return false;
  if (value === 'synthetic-invalid-runtime-token') return false;
  if (JWT_RE.test(value)) return true;
  // UUIDs (8-4-4-4-12 hex) are identities, not secrets.
  if (/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(value)) return false;
  // Hex digests (SHA256 manifest/code hashes) are not secrets.
  if (/^[0-9a-fA-F]{32,128}$/.test(value)) return false;
  if (value.length >= 40 && B64_SECRET_RE.test(value)) return true;
  return false;
}

export function containsTokenShaped(value) {
  if (typeof value !== 'string') return false;
  if (isTokenShaped(value)) return true;
  // Scan substrings for embedded JWTs (e.g. Bearer <jwt> in a header dump).
  const jwtMatch = value.match(/[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/);
  if (jwtMatch && jwtMatch[0] !== DUMMY_CREDENTIAL) return true;
  return false;
}

// Pure gateway decision, reusing egress-allowlist logic.
// Returns {action:'inject-test-api'|'forward-external'|'reject', reason?}
export function decideGatewayAction(urlString, egress) {
  let url;
  try { url = new URL(urlString); }
  catch { return {action: 'reject', reason: 'unlisted'}; }
  if (!isUrlAllowed(urlString, egress)) {
    const bad = egressRejection(egress);
    return {action: 'reject', reason: bad ? bad.reason : 'unlisted'};
  }
  const hostname = url.hostname.toLowerCase();
  if (hostname === VIRTUAL_TEST_HOST) return {action: 'inject-test-api'};
  return {action: 'forward-external'};
}

export function sanitizedChildContext(context) {
  return {...context, engine_token: DUMMY_CREDENTIAL, engine_refresh_token: DUMMY_CREDENTIAL};
}

export function injectionHeaders(incoming, runToken, devAuth) {
  const headers = new Headers(incoming);
  headers.delete('Host');
  headers.delete('Cookie');
  headers.delete('X-Dev-Auth');
  headers.delete('X-Qualification-Auth');
  headers.delete('Authorization');
  headers.set('Authorization', 'Bearer ' + runToken);
  headers.set('X-Qualification-Auth', devAuth ?? '');
  headers.set('User-Agent', 'bifrost-pyodide-dev-executor/1.0');
  return headers;
}

export function externalHeaders(incoming) {
  const headers = new Headers(incoming);
  headers.delete('Host');
  headers.delete('Authorization');
  headers.delete('Cookie');
  headers.delete('X-Dev-Auth');
  headers.delete('X-Qualification-Auth');
  headers.set('User-Agent', 'bifrost-pyodide-dev-executor/1.0');
  return headers;
}
