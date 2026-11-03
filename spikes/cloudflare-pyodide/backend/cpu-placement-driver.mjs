// Native Node observer remains alive across all child calls, including failures.
import http from 'node:http';
import {writeFile} from 'node:fs/promises';
import {CPU_CASES, classifyCpuPlacement} from './cpu-placement.mjs';

if (process.env.CPU_PLACEMENT_LOCAL !== 'true') throw new Error('Explicit isolated local qualification required');
const observations = Object.fromEntries(CPU_CASES.map(name => [name, []]));
const started = performance.now();
const server = http.createServer(async (request, response) => {
  let body = '';
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 256) { response.writeHead(413).end(); return; }
  }
  try {
    const value = JSON.parse(body);
    if (request.method !== 'POST' || request.url !== '/events' || !CPU_CASES.includes(value.case_id) ||
        !['before', 'caught', 'after'].includes(value.event) || observations[value.case_id].length >= 8) {
      response.writeHead(422).end(); return;
    }
    observations[value.case_id].push({event: value.event, elapsed_ms: performance.now() - started});
    response.writeHead(204).end();
  } catch { response.writeHead(400).end(); }
});
await new Promise(resolve => server.listen(8815, '127.0.0.1', resolve));
const outcomes = [];
try {
  for (const name of CPU_CASES) {
    const response = await fetch(`http://127.0.0.1:8814/${name}`, {method: 'POST', signal: AbortSignal.timeout(30000)});
    outcomes.push({case_id: name, http_status: response.status, body: await response.json()});
  }
  await new Promise(resolve => setTimeout(resolve, 500));
  const classifications = CPU_CASES.map(name => classifyCpuPlacement(name, observations[name].map(value => value.event)));
  const report = {runtime: 'local-workerd', cloud_invocations: 0, outcomes, observations, classifications,
    physical_termination_verified: false, elapsed_ms: performance.now() - started};
  await writeFile('/qualification/observation.json', JSON.stringify(report, null, 2) + '\n', {flag: 'wx'});
  if (classifications[0].classification !== 'observer_control_passed' || outcomes.some(value => value.http_status !== 200)) {
    throw new Error('Local API/observer qualification failed');
  }
  for (const outcome of outcomes) {
    const result = outcome.body.body;
    if (JSON.stringify(result?.abi) !== JSON.stringify({python: [3, 14, 2], pyodide: '314.0.6', soabi: 'cpython-314-wasm32-emscripten'})) {
      throw new Error('Pinned Python ABI qualification failed');
    }
    if ('total' in result && !observations[outcome.case_id].some(value => value.event === 'after')) {
      throw new Error('Completed loop missing independently observed after event');
    }
  }
} finally {
  await new Promise(resolve => server.close(resolve));
}
