import { defineConfig } from "cf/config";

export default defineConfig({
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
