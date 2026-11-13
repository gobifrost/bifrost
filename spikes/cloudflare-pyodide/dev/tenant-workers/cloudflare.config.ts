import {defineConfig} from 'cf/config';

// Cloud acceptance config for the per-tenant persistent-worker prototype.
// Deploy with:
//   cf deploy --secrets-file "$env:TEMP/bifrost-pyodide-tenant-ddf316c1-secrets.json"
// The secret file supplies SPIKE_AUTH. LOCAL_PROBE is intentionally absent, so
// the entry requires X-Spike-Auth inside an unexpired SPIKE_EXPIRES_AT window.
export default defineConfig({
  worker: {
    name: 'bifrost-pyodide-tenant-ddf316c1',
    entrypoint: './tenant-entry.mjs',
    compatibilityDate: '2026-10-30',
    workersDev: true,
    previewUrls: false,
    env: {
      LOADER: {type: 'worker-loader'},
      SPIKE_EXPIRES_AT: {type: 'text', value: '1794563405678'},
    },
    limits: {cpuMs: 1000},
    observability: {enabled: false},
  },
});
