// Declared-vs-observed workspace import reconciliation (audit only).
// Recorded paths come from the virtual loader (workspace loads only);
// declared modules come from the attempt's requirements (manifest keys).
// Never blocks here: the caller reports the audit and decides separately.
// Returns null when nothing was declared (old behavior unchanged).
export function reconcileImports(declaredModules, recordedPaths) {
  if (declaredModules === undefined) return null;
  const declared = [...new Set(declaredModules)];
  if (!Array.isArray(recordedPaths)) return {declared, recorded: null, undeclared: null};
  const recorded = [...new Set(recordedPaths)];
  const declaredSet = new Set(declared);
  return {declared, recorded, undeclared: recorded.filter((p) => !declaredSet.has(p))};
}
