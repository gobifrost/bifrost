// Executor output-budget contract: oversized child envelopes fail closed as
// infrastructure failure, never as workflow results. Run with:
// node --test output-budget.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import {OUTPUT_BUDGET_BYTES, ExecutorBodyTooLargeError, readBudgetedJson, readBudgetedText} from './output-budget.mjs';

const jsonResponse = (text) => new Response(text, {headers: {'content-type': 'application/json'}});

test('small child envelope parses under budget', async () => {
  assert.deepEqual(await readBudgetedJson(jsonResponse('{"result":70}')), {result: 70});
});

test('envelope exactly at the budget parses', async () => {
  const exact = 'a'.repeat(OUTPUT_BUDGET_BYTES);
  assert.equal(await readBudgetedText(new Response(exact)), exact);
  await assert.rejects(readBudgetedText(new Response(exact + 'a')), ExecutorBodyTooLargeError);
});

test('oversized child envelope throws without the payload', async () => {
  const secret = 'S3CR3T-' + 'x'.repeat(2 * 1024 * 1024);
  const error = await readBudgetedJson(jsonResponse(JSON.stringify({big: secret}))).then(
    () => { throw new Error('must not resolve'); },
    (error) => error);
  assert.ok(error instanceof ExecutorBodyTooLargeError, `expected ExecutorBodyTooLargeError, got ${error?.name}`);
  assert.equal(error.limitBytes, OUTPUT_BUDGET_BYTES);
  assert.ok(error.observedBytes > OUTPUT_BUDGET_BYTES);
  assert.ok(!String(error.message).includes('S3CR3T'), 'error message must not carry payload bytes');
  assert.ok(!String(JSON.stringify({limit: error.limitBytes, observed: error.observedBytes})).includes('S3CR3T'));
});

test('default budget is one mebibyte', () => {
  assert.equal(OUTPUT_BUDGET_BYTES, 1024 * 1024);
});

test('invalid JSON still surfaces as a parse failure, not a budget failure', async () => {
  await assert.rejects(readBudgetedJson(jsonResponse('not json{')), SyntaxError);
});

test('empty child body keeps existing parse-failure behavior', async () => {
  await assert.rejects(readBudgetedJson(new Response('')), SyntaxError);
});

test('custom budget is respected', async () => {
  assert.equal(await readBudgetedText(new Response('123456'), 6), '123456');
  await assert.rejects(readBudgetedText(new Response('1234567'), 6), ExecutorBodyTooLargeError);
});
