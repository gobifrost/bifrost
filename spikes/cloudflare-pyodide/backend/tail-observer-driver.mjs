import http from 'node:http';
import {randomUUID} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
if (process.env.REQUEST_SIGNAL_MODE !== 'local') throw new Error('Explicit local tail qualification required');
const cases = new Map();
const server = http.createServer(async (request, response) => {
  let text = '';
  for await (const chunk of request) {
    text += chunk;
    if (text.length > 8192) {response.writeHead(413).end(); return;}
  }
  try {
    if (text.includes('synthetic-tail-secret')) throw new Error('Unredacted diagnostic withheld');
    const value = JSON.parse(text);
    const row = cases.get(value.id);
    if (!row || request.method !== 'POST' || request.url !== '/events' || row.events.length >= 20 ||
        !['before', 'after', 'caught', 'background_after', 'parent_abort_requested', 'native_tail'].includes(value.event)) {
      response.writeHead(422).end(); return;
    }
    row.events.push({event: value.event, elapsed_ms: performance.now() - row.started, metadata: value.metadata});
    response.writeHead(204).end();
  } catch {response.writeHead(400).end();}
});
await new Promise(resolve => server.listen(8819, '127.0.0.1', resolve));
try {
  for (const mode of ['js_completion', 'js_background', 'js_io_abort', 'python_io', 'python_cpu']) {
    const row = {id: randomUUID(), mode, started: performance.now(), events: []};
    cases.set(row.id, row);
    const response = await fetch('http://127.0.0.1:8818/', {method: 'POST', body: JSON.stringify({id: row.id, mode}),
      signal: AbortSignal.timeout(45000)});
    row.outcome = {status: response.status, body: await response.json()};
    row.response_elapsed_ms = performance.now() - row.started;
    const deadline = performance.now() + 3000;
    while (!row.events.some(event => event.event === 'native_tail') && performance.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    if (!row.events.some(event => event.event === 'native_tail')) break; // Missing control: don't spend on Python startup.
  }
  await new Promise(resolve => setTimeout(resolve, 500));
  await writeFile('/qualification/observation.json', JSON.stringify({runtime: 'local-workerd', cloud_invocations: 0,
    physical_termination_verified: false,
    records: [...cases.values()].map(({id, mode, events, outcome, response_elapsed_ms}) => ({id, mode, events, outcome, response_elapsed_ms}))}, null, 2) + '\n', {flag: 'wx'});
} finally {await new Promise(resolve => server.close(resolve));}
