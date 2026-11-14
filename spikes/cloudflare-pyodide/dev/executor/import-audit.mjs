// Declared-vs-observed workspace import reconciliation plus post-hoc denial.
// Recorded paths come from the virtual loader (workspace loads only);
// declared modules come from the attempt's requirements (manifest keys).
// reconcileImports never blocks (audit only). deniedTerminal maps a
// non-empty undeclared list to an infra terminal: the run executed, so this
// is post-hoc mapping, not preemption — effects may already exist.
export function reconcileImports(declaredModules, recordedPaths) {
  if (declaredModules === undefined) return null;
  const declared = [...new Set(declaredModules)];
  if (!Array.isArray(recordedPaths)) return {declared, recorded: null, undeclared: null};
  const recorded = [...new Set(recordedPaths)];
  const declaredSet = new Set(declared);
  return {declared, recorded, undeclared: recorded.filter((p) => !declaredSet.has(p))};
}

export class ExecutorUndeclaredImportError extends Error {
  constructor(undeclaredModules, declaredCount, recordedCount) {
    super('Executor ran modules outside its declared requirements; effects may have occurred');
    this.name = 'ExecutorUndeclaredImportError';
    this.undeclaredModules = [...undeclaredModules];
    this.declaredCount = declaredCount;
    this.recordedCount = recordedCount;
  }
}

export function deniedTerminal(audit) {
  if (!audit) return null;
  const undeclared = audit.undeclared;
  if (!Array.isArray(undeclared) || undeclared.length === 0) return null;
  const declaredCount = Array.isArray(audit.declared) ? audit.declared.length : 0;
  const recordedCount = Array.isArray(audit.recorded) ? audit.recorded.length : 0;
  return {
    phase: 'undeclared-import',
    error_type: 'ExecutorUndeclaredImportError',
    error: 'Executor ran modules outside its declared requirements; effects may have occurred',
    provider_outcome: {
      kind: 'terminal_undeclared_import',
      engine_outcome_delivered: true,
      undeclared_modules: [...undeclared],
      declared_count: declaredCount,
      recorded_count: recordedCount,
    },
  };
}
