import {defineConfig} from 'cf/config';
export default defineConfig({worker: {
  name: 'bifrost-pyodide-local-public-url-ddf316c1', entrypoint: './entry.mjs',
  compatibilityDate: '2026-10-30', workersDev: false, previewUrls: false,
  env: {LOADER: {type: 'worker-loader'}, LOCAL_PROBE: {type: 'text', value: 'true'},
    PUBLIC_URL: {type: 'text', value: 'https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev'}},
  limits: {cpuMs: 1000}, observability: {enabled: false},
}});
