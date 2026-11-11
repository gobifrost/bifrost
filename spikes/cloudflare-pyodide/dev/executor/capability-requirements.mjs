// Executor capability gate: reject workflows whose declared workspace imports
// are absent from the staged bundle manifest BEFORE any loader, network, or
// SDK effect. Manifest lookup only; no queue/scheduler/DB.
//
// Contract:
// - requirements === undefined -> null (no declaration, old behavior)
// - manifest not a record -> null (no manifest, old behavior unchanged)
// - requirements.modules === undefined -> null (no modules declared)
// - invalid requirements shape -> {missing_modules: []} (fail closed, no echo)
// - all declared modules present -> null (admit)
// - any missing -> {missing_modules: [...]} (reject, names only)
export const MAX_REQUIRED_MODULES = 32;
export const MAX_MODULE_NAME_LENGTH = 256;

const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

export function capabilityRejection(requirements, manifest) {
  if (requirements === undefined) return null;
  if (!isRecord(manifest)) return null;
  if (!isRecord(requirements)) return {missing_modules: []};
  const modules = requirements.modules;
  if (modules === undefined) return null;
  if (!Array.isArray(modules) || modules.length > MAX_REQUIRED_MODULES) return {missing_modules: []};
  for (const name of modules) {
    if (typeof name !== 'string' || name.length < 1 || name.length > MAX_MODULE_NAME_LENGTH) {
      return {missing_modules: []};
    }
  }
  const seen = new Set();
  const missing = [];
  for (const name of modules) {
    if (seen.has(name)) continue;
    seen.add(name);
    if (!Object.hasOwn(manifest, name)) missing.push(name);
  }
  if (missing.length === 0) return null;
  return {missing_modules: missing};
}
