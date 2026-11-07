import {defineConfig} from 'cf/config';
export default defineConfig({worker: {
  name: 'bifrost-pyodide-local-tail-observer-ddf316c1', entrypoint: './tail-observer-entry.mjs',
  compatibilityDate: '2026-10-30', workersDev: false, previewUrls: false,
  env: {LOADER: {type: 'worker-loader'}, LOCAL_PROBE: {type: 'text', value: 'true'}},
  limits: {cpuMs: 1000}, observability: {enabled: false},
}});
