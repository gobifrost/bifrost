// Deployment identity only. This does not grant scheduler/provider admission.
const keys = ['BIFROST_CF_ACCOUNT_ID', 'BIFROST_CF_EXECUTOR_NAME', 'BIFROST_CF_API_WORKER', 'BIFROST_CF_API_ORIGIN'];
const development = Object.freeze({
  accountId: 'b5fc61a1bec847ecc83b3b880b563aaf',
  executorName: 'bifrost-pyodide-spike-06aeb2c8',
  apiWorker: 'bifrost-pyodide-dev-ui-ddf316c1',
  apiOrigin: 'https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev',
});
export function deploymentOptions(env) {
  if (keys.every(key => env[key] === undefined)) return {...development};
  // All-or-nothing prevents accidentally mixing an account with another
  // installation's API origin or Service Binding.
  if (keys.some(key => typeof env[key] !== 'string' || !env[key])) {
    throw new TypeError('Complete Cloudflare deployment identity required');
  }
  if (!/^[a-f0-9]{32}$/.test(env.BIFROST_CF_ACCOUNT_ID) ||
      !/^[a-z0-9][a-z0-9-]{0,62}$/.test(env.BIFROST_CF_EXECUTOR_NAME) ||
      !/^[a-z0-9][a-z0-9-]{0,62}$/.test(env.BIFROST_CF_API_WORKER)) {
    throw new TypeError('Invalid Cloudflare deployment identity');
  }
  const origin = new URL(env.BIFROST_CF_API_ORIGIN);
  if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) {
    throw new TypeError('Root HTTPS API origin required');
  }
  return {accountId: env.BIFROST_CF_ACCOUNT_ID, executorName: env.BIFROST_CF_EXECUTOR_NAME,
    apiWorker: env.BIFROST_CF_API_WORKER, apiOrigin: origin.origin};
}
