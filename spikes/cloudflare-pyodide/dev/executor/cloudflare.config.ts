import {defineConfig} from 'cf/config';
import {deploymentOptions} from './deployment-options.mjs';
const deployment = deploymentOptions(process.env);
export default defineConfig({
  accountId: deployment.accountId,
  worker: {
    name: deployment.executorName,
    entrypoint: './entry.mjs', compatibilityDate: '2026-10-30',
    workersDev: process.env.DEV_EXECUTOR_OPEN === 'true', previewUrls: false,
    env: {LOADER: {type: 'worker-loader'}, DEV_API: {type: 'worker', worker: deployment.apiWorker},
      API_ORIGIN: {type: 'text', value: deployment.apiOrigin},
      DEV_EXPIRES_AT: {type: 'text', value: process.env.DEV_EXECUTOR_EXPIRES_AT ?? '0'}},
    limits: {cpuMs: 1000}, observability: {enabled: false},
  },
});
