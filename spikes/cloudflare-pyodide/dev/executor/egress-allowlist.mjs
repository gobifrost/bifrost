// Executor egress gate: per-attempt declared HTTPS allowlist enforced BEFORE
// any network effect. Declaration rides the attempt; no queue/scheduler/DB,
// no per-tenant persistence.
//
// Contract:
// - egress === undefined -> null (no declaration, old behavior unchanged)
// - egress.hosts === undefined -> null (no hosts declared, old behavior)
// - invalid egress shape -> {reason: 'egress'|'hosts'|'hostname'} (fail closed, no echo)
// - all checks pass -> null (admit)
// - Enforcement: test-api.invalid always allowed (SDK routes only, via apiTarget).
//   Declared hosts (lowercase normalized) allowed for direct HTTPS fetch.
//   Anything else -> egress-rejected, zero api_calls, no host/value echo.
export const MAX_EGRESS_HOSTS = 8;
export const MAX_HOSTNAME_LENGTH = 253;

export const VIRTUAL_TEST_HOST = 'test-api.invalid';

const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

// Strict hostname: labels 1-63 chars, alphanum + hyphen, no leading/trailing hyphen,
// dots separate labels, total 1-253, must contain a dot, no IPs, no brackets.
// Lowercase input expected; caller normalizes before checking.
const HOSTNAME_RE = /^(?=.{1,253}$)(?!.*\.\.)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;

function isValidHostname(normalized) {
  if (typeof normalized !== 'string' || normalized.length < 1 || normalized.length > MAX_HOSTNAME_LENGTH) return false;
  if (!HOSTNAME_RE.test(normalized)) return false;
  if (/^[\d.]+$/.test(normalized)) return false;
  return true;
}

export function normalizedEgressHosts(egress) {
  if (egress === undefined) return [];
  if (!isRecord(egress)) return null;
  const hosts = egress.hosts;
  if (hosts === undefined) return [];
  if (!Array.isArray(hosts)) return null;
  const out = [];
  for (const h of hosts) {
    if (typeof h !== 'string') return null;
    const lower = h.toLowerCase();
    if (!isValidHostname(lower)) return null;
    out.push(lower);
  }
  return out;
}

export function egressRejection(egress) {
  if (egress === undefined) return null;
  if (!isRecord(egress)) return {reason: 'egress'};
  const hosts = egress.hosts;
  if (hosts === undefined) return null;
  if (!Array.isArray(hosts) || hosts.length > MAX_EGRESS_HOSTS) return {reason: 'hosts'};
  for (const h of hosts) {
    if (typeof h !== 'string' || h.length < 1 || h.length > MAX_HOSTNAME_LENGTH) return {reason: 'hostname'};
    const lower = h.toLowerCase();
    // Reject credentials/ports/path/query/fragment explicitly before hostname regex,
    // so shape class stays 'hostname' without echoing values.
    if (/[@:/?#\s\[\]*]/.test(h)) return {reason: 'hostname'};
    if (h !== h.trim()) return {reason: 'hostname'};
    try {
      // URL parse guard: hostname-only declarations must not parse as URL with auth/port.
      if (h.includes('://')) return {reason: 'hostname'};
    } catch { return {reason: 'hostname'}; }
    if (!isValidHostname(lower)) return {reason: 'hostname'};
  }
  return null;
}

export function isHostAllowed(hostname, egress) {
  if (typeof hostname !== 'string') return false;
  const lower = hostname.toLowerCase();
  if (lower === VIRTUAL_TEST_HOST) return true;
  const normalized = normalizedEgressHosts(egress);
  if (normalized === null) return false;
  return normalized.includes(lower);
}

export function isUrlAllowed(urlString, egress) {
  let url;
  try { url = new URL(urlString); }
  catch { return false; }
  if (url.protocol !== 'https:') return false;
  if (url.username || url.password) return false;
  // Default HTTPS port only; declared hosts carry no ports.
  // WHATWG URL strips default :443, so explicit :443 still yields ''.
  if (url.port !== '') return false;
  return isHostAllowed(url.hostname, egress);
}
