import {defineConfig} from 'cf/config';
export default defineConfig({worker: {
  name: 'bifrost-pyodide-local-disposal-ddf316c1', entrypoint: './guest-disposal-entry.mjs',
  compatibilityDate: '2026-10-30', workersDev: false, previewUrls: false,
  env: {LOADER: {type: 'worker-loader'}, LOCAL_PROBE: {type: 'text', value: 'true'}},
  limits: {cpuMs: 1000}, observability: {enabled: false},
}});
