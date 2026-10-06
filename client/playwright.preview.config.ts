import { defineConfig, devices } from "@playwright/test";

/**
 * Development-only What's New preview against a running debug Vite stack.
 * It deliberately has no production-client setup or CI project.
 */
export default defineConfig({
	testDir: "./e2e/preview",
	testMatch: /.*\.admin\.spec\.ts$/,
	outputDir: "./playwright-results/preview",
	forbidOnly: true,
	retries: 0,
	timeout: 30_000,
	use: {
		baseURL: process.env.BIFROST_PREVIEW_URL,
		trace: "off",
		screenshot: "on",
	},
	projects: [
		{
			name: "development-preview",
			use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
		},
	],
});
