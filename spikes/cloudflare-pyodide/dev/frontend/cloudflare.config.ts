import {defineConfig} from 'cf/config';
export default defineConfig({
  accountId: 'b5fc61a1bec847ecc83b3b880b563aaf',
  worker: {
    name: 'bifrost-pyodide-dev-ui-ddf316c1',
    entrypoint: './edge.mjs',
    compatibilityDate: '2026-10-30',
    workersDev: true,
    previewUrls: false,
    env: {
      ASSETS: {type: 'assets'},
      API_ORIGIN: {type: 'text', value: process.env.DEV_API_ORIGIN ?? ''},
      DEV_EXPIRES_AT: {type: 'text', value: process.env.DEV_EXPIRES_AT ?? '0'},
    },
    assets: {notFoundHandling: 'single-page-application', runWorkerFirst: true},
    limits: {cpuMs: 1000},
    observability: {enabled: false},
  },
});
