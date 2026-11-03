import {defineConfig} from 'cf/config';
export default defineConfig({
  accountId: 'b5fc61a1bec847ecc83b3b880b563aaf',
  worker: {
    name: 'bifrost-pyodide-spike-ddf316c1', entrypoint: './entry.mjs', compatibilityDate: '2026-10-30',
    workersDev: process.env.DEV_EXECUTOR_OPEN === 'true', previewUrls: false,
    env: {LOADER: {type: 'worker-loader'}, DEV_API: {type: 'worker', worker: 'bifrost-pyodide-dev-ui-ddf316c1'},
      DEV_EXPIRES_AT: {type: 'text', value: process.env.DEV_EXECUTOR_EXPIRES_AT ?? '0'}},
    limits: {cpuMs: 1000}, observability: {enabled: false},
  },
});
