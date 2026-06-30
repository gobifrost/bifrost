import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E Test Configuration
 *
 * Multi-project setup with different auth states:
 * - setup: Creates users and saves auth states
 * - platform-admin: Tests requiring admin access
 * - org-user: Tests for regular org users
 * - unauthenticated: Tests for login flow and unauthenticated access
 * - chromium: Default project for general tests (uses admin auth)
 *
 * @see https://playwright.dev/docs/test-configuration
 */
export default defineConfig({
	testDir: "./e2e",
	fullyParallel: true,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 2 : 0,
	workers: process.env.CI ? 1 : 4,
	timeout: 30000,

	reporter: [
		["list"],
		["html", { outputFolder: "playwright-results/html", open: "never" }],
		["json", { outputFile: "playwright-results/results.json" }],
	],

	use: {
		// Use environment variable for Docker, fallback to localhost for local dev
		baseURL: process.env.TEST_BASE_URL || "http://localhost:3000",
		trace: "on-first-retry",
		// PLAYWRIGHT_SCREENSHOT_ALL=1 (set by `./test.sh client e2e --screenshots`)
		// captures a screenshot for every test instead of only on failure.
		// Used by the bifrost-testing skill's UX review workflow.
		screenshot:
			process.env.PLAYWRIGHT_SCREENSHOT_ALL === "1"
				? "on"
				: "only-on-failure",
		video: "on-first-retry",
	},

	projects: [
		// =============================================================
		// Setup project - runs first to create users and save auth state
		// No retries - database state can't be reset between retries
		// =============================================================
		{
			name: "setup",
			testMatch: /setup\/global\.setup\.ts/,
			retries: 0,
		},

		// =============================================================
		// Platform admin tests (.admin.spec.ts files)
		// Uses platform_admin auth state for full system access
		// =============================================================
		{
			name: "platform-admin",
			use: {
				...devices["Desktop Chrome"],
				storageState: "e2e/.auth/platform_admin.json",
			},
			dependencies: ["setup"],
			testMatch: /.*\.admin\.spec\.ts$/,
		},

		// =============================================================
		// Org user tests (.user.spec.ts files)
		// Uses org1_user auth state for permission testing
		// =============================================================
		{
			name: "org-user",
			use: {
				...devices["Desktop Chrome"],
				storageState: "e2e/.auth/org1_user.json",
			},
			dependencies: ["setup"],
			testMatch: /.*\.user\.spec\.ts$/,
		},

		// =============================================================
		// Unauthenticated tests (.unauth.spec.ts files)
		// No auth state - tests login flow and access control
		// =============================================================
		{
			name: "unauthenticated",
			use: {
				...devices["Desktop Chrome"],
				// No storageState - starts with clean browser
			},
			dependencies: ["setup"],
			testMatch: /.*\.unauth\.spec\.ts$/,
		},

		// =============================================================
		// Default project for general tests (not matching other patterns)
		// Uses platform_admin auth state
		// =============================================================
		{
			name: "chromium",
			use: {
				...devices["Desktop Chrome"],
				storageState: "e2e/.auth/platform_admin.json",
			},
			dependencies: ["setup"],
			// Match all .spec.ts files EXCEPT .admin, .user, .unauth, .docs patterns
			testMatch:
				/^(?!.*\.(admin|user|unauth|docs)\.spec\.ts$).*\.spec\.ts$/,
		},

		// =============================================================
		// Docs screenshot pipeline (.docs.spec.ts files)
		// Drives the screenshots.yaml manifest in gobifrost
		// to capture full-page screenshots, with sharp-based crop/callout
		// post-processing. Per-entry auth is handled inside the spec via
		// ensureAuthenticated() so a single project can capture under
		// platform_admin, org user, or unauthenticated states.
		// =============================================================
		{
			name: "docs",
			use: {
				...devices["Desktop Chrome"],
				viewport: { width: 1440, height: 900 },
			},
			dependencies: ["setup"],
			testMatch: /.*\.docs\.spec\.ts$/,
			retries: 0,
			workers: 1,
		},
	],

	// No webServer config when running in Docker - services are started by docker-compose
	// For local development, start the dev server manually or use the original config
	...(process.env.CI
		? {}
		: {
				webServer: {
					command: "npm run dev",
					url: "http://localhost:3000",
					reuseExistingServer: true,
					timeout: 120 * 1000,
				},
			}),
});
