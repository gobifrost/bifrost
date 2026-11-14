import {bindings, defineConfig, exports} from 'cf/config';

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
    exports: {
      // Attempt-scoped token broker: the child holds an opaque handle, the DO
      // holds the real run token for the duration of the attempt.
      RunTokenBroker: exports.durableObject({storage: 'sqlite'}),
    },
    env: {
      LOADER: {type: 'worker-loader'},
      SPIKE_EXPIRES_AT: {type: 'text', value: process.env.SPIKE_EXPIRES_AT ?? '0'},
      // Staging only: when set, the supervisor fetches modules from this real
      // Bifrost API instead of an attempt-supplied store.
      API_ORIGIN: {type: 'text', value: process.env.STAGING_API_ORIGIN ?? ''},
      TOKENS: bindings.durableObject({
        worker: 'bifrost-pyodide-tenant-ddf316c1',
        exportName: 'RunTokenBroker',
      }),
    },
    limits: {cpuMs: 1000},
    observability: {enabled: false},
  },
});
