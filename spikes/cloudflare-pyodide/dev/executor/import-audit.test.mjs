// Declared-vs-observed import reconciliation contract (audit only, never
// blocks). Run with: node --test import-audit.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import {reconcileImports, deniedTerminal, ExecutorUndeclaredImportError} from './import-audit.mjs';

test('no declaration returns null (old behavior unchanged)', () => {
  assert.equal(reconcileImports(undefined, ['a.py']), null);
  assert.equal(reconcileImports(undefined, undefined), null);
});

test('fully declared run reports empty undeclared', () => {
  assert.deepEqual(reconcileImports(['w.py', 'h.py'], ['h.py', 'w.py', 'h.py']), {
    declared: ['w.py', 'h.py'], recorded: ['h.py', 'w.py'], undeclared: []});
});

test('observed-but-undeclared workspace module is listed', () => {
  const audit = reconcileImports(['w.py'], ['w.py', 'evil.py']);
  assert.deepEqual(audit.undeclared, ['evil.py']);
  assert.deepEqual(audit.recorded, ['w.py', 'evil.py']);
});

test('missing recorder output keeps declared context without failing', () => {
  assert.deepEqual(reconcileImports(['w.py'], undefined),
    {declared: ['w.py'], recorded: null, undeclared: null});
  assert.deepEqual(reconcileImports(['w.py'], null),
    {declared: ['w.py'], recorded: null, undeclared: null});
});

test('duplicate observations are deduplicated', () => {
  assert.deepEqual(reconcileImports(['w.py'], ['w.py', 'w.py']).recorded, ['w.py']);
});

test('deniedTerminal returns null without undeclared imports', () => {
  assert.equal(deniedTerminal(null), null);
  assert.equal(deniedTerminal(undefined), null);
  assert.equal(deniedTerminal({declared: ['w.py'], recorded: ['w.py'], undeclared: []}), null);
  assert.equal(deniedTerminal({declared: ['w.py'], recorded: null, undeclared: null}), null);
});

test('deniedTerminal maps undeclared imports to the infra terminal', () => {
  const audit = reconcileImports(['w.py'], ['w.py', 'evil.py']);
  const terminal = deniedTerminal(audit);
  assert.equal(terminal.phase, 'undeclared-import');
  assert.equal(terminal.error_type, 'ExecutorUndeclaredImportError');
  assert.ok(terminal.error.includes('effects may have occurred'));
  assert.equal(terminal.provider_outcome.kind, 'terminal_undeclared_import');
  assert.equal(terminal.provider_outcome.engine_outcome_delivered, true);
  assert.deepEqual(terminal.provider_outcome.undeclared_modules, ['evil.py']);
  assert.equal(terminal.provider_outcome.declared_count, 1);
  assert.equal(terminal.provider_outcome.recorded_count, 2);
});

test('deniedTerminal counts honestly and names only modules', () => {
  const terminal = deniedTerminal({declared: [], recorded: ['h.py'], undeclared: ['h.py']});
  assert.equal(terminal.provider_outcome.declared_count, 0);
  assert.equal(terminal.provider_outcome.recorded_count, 1);
  const encoded = JSON.stringify(terminal);
  assert.ok(!encoded.includes('S3CR3T'), 'terminal must not carry secrets');
});

test('deniedTerminal tolerates malformed audit shapes', () => {
  assert.equal(deniedTerminal({}), null);
  assert.equal(deniedTerminal({undeclared: 'evil.py'}), null);
  assert.equal(deniedTerminal({undeclared: []}), null);
});

test('ExecutorUndeclaredImportError carries counts, not content', () => {
  const error = new ExecutorUndeclaredImportError(['evil.py'], 0, 1);
  assert.equal(error.name, 'ExecutorUndeclaredImportError');
  assert.ok(error.message.includes('effects may have occurred'));
  assert.deepEqual(error.undeclaredModules, ['evil.py']);
  assert.equal(error.declaredCount, 0);
  assert.equal(error.recordedCount, 1);
});

test('deniedTerminal envelope shape matches the executor contract', async () => {
  const terminal = deniedTerminal(reconcileImports(['w.py'], ['w.py', 'evil.py']));
  const response = Response.json({...terminal, api_calls: []}, {status: 502});
  assert.equal(response.status, 502);
  const body = await response.json();
  assert.equal(body.phase, 'undeclared-import');
  assert.deepEqual(body.api_calls, []);
  assert.deepEqual(body.provider_outcome.undeclared_modules, ['evil.py']);
});
