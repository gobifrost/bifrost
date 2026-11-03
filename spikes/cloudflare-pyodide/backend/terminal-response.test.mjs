import assert from 'node:assert/strict';
import test from 'node:test';
import {terminalResponse} from './terminal-response.mjs';
import {boundedLosslessJson} from './lossless-json.mjs';

test('the previous retained-body reader cancels before EOF on the same oversized response', async () => {
  let pulls = 0, canceled = false;
  const response = new Response(new ReadableStream({pull(controller) {
    if (++pulls <= 20) controller.enqueue(new Uint8Array(65536));
    else controller.close();
  }, cancel() {canceled = true;}}));
  await assert.rejects(boundedLosslessJson(response), /byte limit/);
  assert.ok(pulls < 21);
  assert.equal(canceled, true);
});

test('oversized response is discarded only after actual EOF without canceling the completed stream', async () => {
  let pulls = 0, canceled = false;
  const response = new Response(new ReadableStream({
    pull(controller) {
      if (++pulls <= 20) controller.enqueue(new Uint8Array(65536));
      else controller.close();
    }, cancel() {canceled = true;},
  }));
  assert.deepEqual(await terminalResponse(response), {oversized: true, response_eof: true, response_bytes: 1310720});
  assert.equal(pulls, 21);
  assert.equal(canceled, false);
});

test('drain ceiling is uncertain and cancels instead of fabricating EOF', async () => {
  let canceled = false;
  const response = new Response(new ReadableStream({
    pull(controller) {controller.enqueue(new Uint8Array(65536));},
    cancel() {canceled = true;},
  }));
  await assert.rejects(terminalResponse(response), /drain budget exceeded; outcome uncertain/);
  assert.equal(canceled, true);
});

test('transport error after the retention bound remains uncertain', async () => {
  let pulls = 0;
  const response = new Response(new ReadableStream({pull(controller) {
    if (++pulls <= 20) controller.enqueue(new Uint8Array(65536));
    else controller.error(new Error('Synthetic lost response'));
  }}));
  await assert.rejects(terminalResponse(response), /Synthetic lost response/);
});

test('fitting JSON retains numeric fidelity and rejects malformed data', async () => {
  const decoded = await terminalResponse(new Response('{"value":9007199254740993}'));
  assert.equal(JSON.stringify(decoded.value), '{"value":9007199254740993}');
  await assert.rejects(terminalResponse(new Response('not JSON')), SyntaxError);
});
