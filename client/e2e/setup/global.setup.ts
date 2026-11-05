/* eslint-disable no-console */
/**
 * Global Setup for Playwright E2E Tests
 *
 * This file runs once before all tests to:
 * 1. Create test users via API (platform_admin, org1_user, org2_user)
 * 2. Set up MFA for each user
 * 3. Authenticate the platform admin in a browser to seed local AI defaults
 */

import { test as setup } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import { createTestUsers, authenticateInBrowser } from "./auth-helpers";
import { seedLocalAIDefaults } from "./ai-fixtures";
import {
	AUTH_STATE_DIR,
	getCredentialsPath,
} from "../fixtures/users";

// ESM equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Increase timeout for setup - user creation takes time
setup.setTimeout(120000);

setup(
	"create users and authenticate",
	{ tag: "@smoke" },
	async ({ browser }) => {
		console.log("\n=== Global Setup: Creating test users ===\n");

		// Ensure auth directory exists (relative to e2e folder)
		const authDir = path.resolve(__dirname, "..", AUTH_STATE_DIR);
		if (!fs.existsSync(authDir)) {
			fs.mkdirSync(authDir, { recursive: true });
		}

		// Check if credentials already exist (users already created)
		const credentialsPath = path.resolve(
			__dirname,
			"..",
			getCredentialsPath(),
		);
		let credentials;

		if (fs.existsSync(credentialsPath)) {
			console.log(
				"Found existing credentials, skipping user creation...",
			);
			credentials = JSON.parse(fs.readFileSync(credentialsPath, "utf-8"));
		} else {
			// Create test users via API
			credentials = await createTestUsers();

			// Save credentials for tests that need TOTP secrets
			fs.writeFileSync(
				credentialsPath,
				JSON.stringify(credentials, null, 2),
			);
			console.log(`\nSaved credentials to ${credentialsPath}`);
		}

		console.log("\n=== Seeding Local AI Defaults ===\n");
		const context = await browser.newContext();
		const page = await context.newPage();
		try {
			await authenticateInBrowser(page, credentials.platform_admin);
			await seedLocalAIDefaults(context);
		} finally {
			await context.close();
		}

		console.log("\n=== Global Setup Complete ===\n");
	},
);
