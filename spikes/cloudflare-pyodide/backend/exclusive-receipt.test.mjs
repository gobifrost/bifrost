import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {withExclusiveReceipt} from './exclusive-receipt.mjs';

test('only one concurrent observer may invoke effects', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pyodide-receipt-'));
  try {
    const path = join(directory, 'receipt.json');
    let calls = 0;
    const results = await Promise.allSettled(Array.from({length: 8}, () =>
      withExclusiveReceipt(path, async save => {
        calls++;
        await save({long: 'x'.repeat(1024)});
        await save({done: true});
      })));
    assert.equal(calls, 1);
    assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
    for (const result of results.filter(r => r.status === 'rejected')) {
      assert.equal(result.reason.code, 'EEXIST');
    }
    assert.deepEqual(JSON.parse(await readFile(path, 'utf8')), {done: true});
  } finally {
    await rm(directory, {recursive: true});
  }
});

test('failure retains reservation and prevents another invocation', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pyodide-receipt-'));
  try {
    const path = join(directory, 'receipt.json');
    await assert.rejects(withExclusiveReceipt(path, async () => {
      throw new Error('fixed interruption');
    }), /fixed interruption/);
    assert.deepEqual(JSON.parse(await readFile(path, 'utf8')),
      {state: 'reserved', completion_verified: false});
    await assert.rejects(withExclusiveReceipt(path, () => assert.fail('must not invoke')),
      {code: 'EEXIST'});
  } finally {
    await rm(directory, {recursive: true});
  }
});
