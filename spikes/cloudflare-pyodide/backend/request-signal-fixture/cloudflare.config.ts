import {defineConfig} from 'cf/config';
export default defineConfig({worker: {
  name: 'bifrost-pyodide-local-request-signal-ddf316c1', entrypoint: './request-signal-entry.mjs',
  compatibilityDate: '2026-10-30',
  compatibilityFlags: process.env.REQUEST_SIGNAL_MODE === 'enabled' ? ['enable_request_signal'] : [],
  workersDev: false, previewUrls: false,
  env: {LOADER: {type: 'worker-loader'}, LOCAL_PROBE: {type: 'text', value: 'true'}},
  limits: {cpuMs: 1000}, observability: {enabled: false},
}});
