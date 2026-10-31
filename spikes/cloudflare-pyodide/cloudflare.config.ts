import { defineConfig } from "cf/config";

export default defineConfig(({mode}) => ['isolation','isolation-cloud','engine-cloud'].includes(mode ?? '') ? {
	worker: {
		name: 'bifrost-pyodide-spike-ddf316c1',
		entrypoint: mode === 'engine-cloud' ? 'backend/cloud-engine-entry.mjs' : 'backend/isolation-entry.mjs',
		compatibilityDate: '2026-10-30',
		workersDev: mode === 'isolation-cloud' || mode === 'engine-cloud',
		previewUrls: false,
		env: {LOADER: {type: 'worker-loader'}, LOCAL_PROBE: {type: 'text', value: 'false'},
			SPIKE_EXPIRES_AT: {type:'text',value:process.env.SPIKE_EXPIRES_AT ?? '0'},
			PARENT_CANARY: {type:'text',value:'synthetic-supervisor-only'}},
		limits: {cpuMs: 1000},
		observability: {enabled: false},
	},
} : {
	worker: {
		name: "bifrost-pyodide-spike-ddf316c1",
		compatibilityDate: "2026-10-30",
		compatibilityFlags: [
			"python_workers",
		],
		entrypoint: "src/entry.py",
		workersDev: false,
		previewUrls: false,
		limits: { cpuMs: 1000 },
		observability: {
			enabled: false,
		},
	},
});
