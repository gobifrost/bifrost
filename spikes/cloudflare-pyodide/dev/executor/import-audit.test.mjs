// Declared-vs-observed import reconciliation contract (audit only, never
// blocks). Run with: node --test import-audit.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import {reconcileImports} from './import-audit.mjs';

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
