/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
	plugins: [react()],
	resolve: {
		alias: {
			"@": path.resolve(__dirname, "./src"),
		},
	},
	test: {
		environment: "happy-dom",
		globals: true,
		setupFiles: ["./src/test/setup.ts"],
		include: ["src/**/*.test.{ts,tsx}"],
		// Exclude Playwright e2e specs so they don't accidentally run here
		exclude: ["e2e/**", "node_modules/**", "dist/**"],
		// happy-dom suites are CPU-heavy; leave capacity for each worker so
		// interaction tests do not lose their entire timeout to contention.
		maxWorkers: "50%",
		css: false,
	},
});
