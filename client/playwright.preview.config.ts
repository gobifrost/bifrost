import { defineConfig, devices } from "@playwright/test";

/**
 * Product Updates runtime against the isolated debug stack, including real
 * API/database receipts and a second browser with no local storage.
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
