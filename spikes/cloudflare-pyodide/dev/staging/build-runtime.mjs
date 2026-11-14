// Seam-only rebuild: carry the pinned companion runtime and replace exactly the
// modules the upstream PRs change. Adding the whole upstream src closure pulls
// the application (src.config) into the runtime, which is not runtime-compatible.
// Usage: node build-runtime3.mjs <spike-root> <api-root> <out-file>
import {readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const spike = process.argv[2];
const api = process.argv[3];
const out = process.argv[4];
const old = (await import(pathToFileURL(join(spike, 'dev/runtime/generated-runtime.mjs')).href)).default;

const REPLACE = [
  'src/services/execution/engine.py',
  'src/services/execution/worker.py',
  'src/services/execution/result_envelope.py',
  'src/sdk/context.py',
  'bifrost/_execution_context.py',
];

const resolved = new Map(Object.entries(old));
for (const key of REPLACE) {
  const text = await readFile(join(api, key), 'utf8');
  resolved.set(key, {py: text});
}

// Companion bootstrap: the server-only Redis log path must fail its guard
// instead of importing src.config, which the isolated runtime does not ship.
const runner = resolved.get('runtime_runner.py').py;
if (process.env.NO_GUARD !== '1' && !runner.includes('_companion_require_server_process')) {
  const marker = 'run_execution = execution_worker.run_execution';
  if (!runner.includes(marker)) throw new Error('runtime_runner shape changed');
  resolved.set('runtime_runner.py', {py: runner.replace(marker, `${marker}
import bifrost._logging as _bifrost_logging


def _companion_require_server_process():
    try:
        from src.core.module_cache_sync import _execution_child
    except Exception:
        _execution_child = False
    from bifrost.client import get_engine_socket_path
    if _execution_child or get_engine_socket_path() is not None:
        raise RuntimeError("Direct log storage access is unavailable in execution children")


_bifrost_logging._require_server_process = _companion_require_server_process`)});
}

const entries = [];
for (const [key, value] of [...resolved].sort(([a], [b]) => a.localeCompare(b))) {
  if (value.py !== undefined) entries.push([key, `{py:${JSON.stringify(value.py)}}`]);
  else if (value.js !== undefined) entries.push([key, `{js:${JSON.stringify(value.js)}}`]);
  else entries.push([key, `{base64:${JSON.stringify(value.base64)}}`]);
}
const body = entries.map(([key, value]) => `${JSON.stringify(key)}:${value}`).join(',\n');
await writeFile(out, `// Generated from ${api} (seam-only: PR-changed modules).\nexport default {\n${body}\n};\n`);

console.log(JSON.stringify({total: entries.length,
  replaced: REPLACE,
  engine_has_stream_capture: resolved.get('src/services/execution/engine.py').py.includes('_capture_execution_streams'),
  engine_has_public_url: resolved.get('src/services/execution/engine.py').py.includes('request.public_url or'),
  worker_has_observer: resolved.get('src/services/execution/worker.py').py.includes('ExecutionResourceObserver')}, null, 2));
