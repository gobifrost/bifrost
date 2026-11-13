// Deterministic org/tenant id -> dynamic-dispatch worker id.
//
// The generic worker script is CONSTANT (see generic-worker-code.mjs), so a
// code change redeploys the same script under the EXISTING ids: no id
// rotation is required on code change. Rotate an id only as a policy decision
// (e.g. to quarantine a tenant into a fresh isolate); that is isolation
// hygiene, not a deployment requirement.
export async function tenantWorkerId(tenantId) {
  if (typeof tenantId !== 'string' || tenantId.length < 1 || tenantId.length > 256) {
    throw new TypeError('Invalid tenant id');
  }
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(tenantId));
  const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
  return `tenant-py-${hex}`;
}
