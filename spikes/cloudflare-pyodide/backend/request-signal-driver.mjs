import http from 'node:http';
import {randomUUID} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
if (!['enabled', 'disabled'].includes(process.env.REQUEST_SIGNAL_MODE)) throw new Error('Explicit local signal mode required');
const records = new Map();
const server = http.createServer(async (request, response) => {
  let text = '';
  for await (const chunk of request) {
    text += chunk;
    if (text.length > 256) { response.writeHead(413).end(); return; }
  }
  try {
    const body = JSON.parse(text);
    const record = records.get(body.id);
    if (request.url !== '/events' || request.method !== 'POST' || !record || record.events.length >= 8 ||
        !['before', 'caught', 'after', 'parent_abort_received'].includes(body.event)) {
      response.writeHead(422).end(); return;
    }
    record.events.push({event: body.event, elapsed_ms: performance.now() - record.started});
    response.writeHead(204).end();
    if (body.event === 'before' && record.mode !== 'control' && !record.timer) {
      record.timer = setTimeout(() => {
        record.events.push({event: 'abort_requested', elapsed_ms: performance.now() - record.started});
        record.controller.abort();
      }, 10);
    }
  } catch { response.writeHead(400).end(); }
});
await new Promise(resolve => server.listen(8817, '127.0.0.1', resolve));
try {
  for (const mode of ['control', 'io_abort', 'cpu_abort']) {
    const record = {id: randomUUID(), mode, events: [], started: performance.now(), controller: new AbortController()};
    records.set(record.id, record);
    try {
      const response = await fetch('http://127.0.0.1:8816/', {method: 'POST', body: JSON.stringify({id: record.id, mode}),
        signal: AbortSignal.any([record.controller.signal, AbortSignal.timeout(20000)])});
      record.outcome = {status: response.status, body: await response.json()};
    } catch (error) { record.outcome = {error_type: error.name}; }
    await new Promise(resolve => setTimeout(resolve, 2500));
    clearTimeout(record.timer);
    if (mode === 'control' && !record.events.some(event => event.event === 'after')) throw new Error('Completion control failed');
  }
  await writeFile('/qualification/observation.json', JSON.stringify({runtime: 'local-workerd',
    request_signal_mode: process.env.REQUEST_SIGNAL_MODE,
    records: [...records.values()].map(({id, mode, events, outcome}) => ({id, mode, events, outcome})),
    physical_termination_verified: false, cloud_invocations: 0}, null, 2) + '\n', {flag: 'wx'});
} finally { await new Promise(resolve => server.close(resolve)); }
