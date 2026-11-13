// In-supervisor module gateway for the per-tenant prototype (local only).
//
// Role mirrors dev/executor's DevApi gateway: workflow module source reaches
// the generic child ONLY as gateway-verified responses to
// GET /api/sdk/modules/<path> and GET /api/sdk/modules-resolve?name=<name>.
// The supervisor never forwards raw body.modules into the child; it forwards
// the {path, content} bytes the gateway served after pin verification.
//
// Differences from the real executor gateway (explicit, local-only):
// - The store is attempt-supplied (body.modules, now PATH-keyed) rather than
//   Redis/S3; the manifest (body.manifest, PATH-keyed) is the pin set.
// - The run token lives only in gateway props (parent side). Callers present
//   the existing DUMMY_CREDENTIAL bearer; the gateway marks injection via the
//   X-Gateway-Injected header and never echoes the token.
// - No cloudflare:workers import: this is a plain object with a fetch method
//   so node --test can exercise it without workerd. tenant-entry.mjs calls it
//   directly in the run path (no extra binding, no second orchestration layer).
import {DUMMY_CREDENTIAL} from '../executor/gateway-injection.mjs';

const HEX64 = /^[0-9a-f]{64}$/;
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z_][A-Za-z0-9_]*)*$/;

const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

export function isStoragePath(path) {
  return typeof path === 'string' &&
    path.length >= 4 &&
    path.endsWith('.py') &&
    !path.startsWith('/') &&
    !path.includes('\\') &&
    !path.includes('\0') &&
    !path.split('/').some((part) => part === '' || part === '.' || part === '..' || part === '.git');
}

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// Logical import name -> storage base path (same shape as the production
// logical_name_to_base_path: dots and slashes both separate segments).
export function basePathForName(name) {
  if (typeof name !== 'string' || !NAME_RE.test(name)) return null;
  const base = name.replaceAll('.', '/').replace(/^\/+|\/+$/g, '');
  if (base === '' || base.includes('//')) return null;
  return base;
}

export function createTenantGateway({store, manifest, runToken} = {}) {
  const sources = isRecord(store) ? store : {};
  const pins = isRecord(manifest) ? manifest : {};
  const token = typeof runToken === 'string' && runToken.length >= 10 ? runToken : null;

  async function resolve(name) {
    const base = basePathForName(name);
    if (base === null) return {kind: 'invalid', path: String(name)};
    for (const [candidate, kind] of [[`${base}.py`, 'module'], [`${base}/__init__.py`, 'package']]) {
      const content = sources[candidate];
      const pin = pins[candidate];
      if (typeof content === 'string' && HEX64.test(pin ?? '')) {
        if ((await sha256Hex(content)) !== pin) continue;
        return {kind, path: candidate, storage_path: candidate, hash: pin};
      }
    }
    if (Object.keys(sources).some((p) => p.startsWith(`${base}/`))) {
      return {kind: 'namespace', path: base};
    }
    return {kind: 'not_found', path: base};
  }

  return {
    // Parent-side only; never sent to the child.
    runToken: token,
    async fetch(request) {
      const url = new URL(request.url);
      if (request.headers.get('Authorization') !== `Bearer ${DUMMY_CREDENTIAL}`) {
        return Response.json({phase: 'gateway-auth', error: 'Child presents dummy credential only'}, {status: 401});
      }
      // Injection marker: the run token was held in props and applied to this
      // gateway decision. The value itself is never echoed or forwarded here.
      const headers = {'X-Gateway-Injected': token === null ? 'false' : 'true'};
      if (url.pathname === '/api/sdk/modules-resolve') {
        const name = url.searchParams.get('name') ?? '';
        return Response.json(await resolve(name), {headers});
      }
      const prefix = '/api/sdk/modules/';
      if (url.pathname.startsWith(prefix)) {
        const path = decodeURIComponent(url.pathname.slice(prefix.length));
        if (!isStoragePath(path)) {
          return Response.json({phase: 'invalid-path', error: 'Invalid module path'}, {status: 400, headers});
        }
        const pin = pins[path];
        if (!HEX64.test(pin ?? '')) {
          return Response.json({phase: 'unpinned', error: 'Workspace module is not pinned'}, {status: 404, headers});
        }
        const content = sources[path];
        if (typeof content !== 'string') {
          return Response.json({phase: 'not-found', error: 'Module not found'}, {status: 404, headers});
        }
        if ((await sha256Hex(content)) !== pin) {
          return Response.json({phase: 'digest-mismatch', error: 'Workspace module digest mismatch'}, {status: 409, headers});
        }
        return Response.json({path, content, hash: pin}, {headers});
      }
      return new Response('Not found', {status: 404});
    },
  };
}
