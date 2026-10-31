import { defineConfig } from "cf/config";

export default defineConfig(({mode}) => mode === 'isolation' ? {
	worker: {
		name: 'bifrost-pyodide-spike-ddf316c1',
		entrypoint: 'backend/isolation-entry.mjs',
		compatibilityDate: '2026-10-30',
		workersDev: false,
		previewUrls: false,
		env: {LOADER: {type: 'worker-loader'}, LOCAL_PROBE: {type: 'text', value: 'false'}},
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
