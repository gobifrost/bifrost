import {defineConfig} from 'cf/config';
export default defineConfig({worker: {
  name: 'bifrost-pyodide-local-canonical-failure-ddf316c1',
  entrypoint: './backend/authorized-local-entry.mjs', compatibilityDate: '2026-10-30',
  workersDev: false, previewUrls: false,
  env: {LOADER: {type: 'worker-loader'}, ISOLATED_TEST_STACK: {type: 'text', value: 'true'}},
  limits: {cpuMs: 1000}, observability: {enabled: false},
}});
