/* eslint-disable no-console */
/**
 * Global Setup for Playwright E2E Tests
 *
 * This file runs once before all tests to:
 * 1. Create test users via API (platform_admin, org1_user, org2_user)
 * 2. Set up MFA for each user
 * 3. Authenticate each user in the browser
 * 4. Save browser storage state for reuse
 *
 * The saved storage state allows tests to start authenticated without
 * going through the login flow each time.
 */

import { test as setup } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import { createTestUsers, authenticateInBrowser } from "./auth-helpers";
import {
	USERS,
	AUTH_STATE_DIR,
	getAuthStatePath,
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

		// Authenticate each user in browser and save storage state
		console.log("\n=== Authenticating users in browser ===\n");

		for (const userKey of Object.keys(USERS)) {
			const userCredentials =
				credentials[userKey as keyof typeof credentials];

			// Skip org entries (they're not users)
			if (!userCredentials || !("email" in userCredentials)) {
				continue;
			}

			console.log(
				`Authenticating ${userKey} (${userCredentials.email})...`,
			);

			// Create new browser context for this user
			const context = await browser.newContext();
			const page = await context.newPage();

			try {
				// Authenticate in browser
				await authenticateInBrowser(page, userCredentials);

				// Save storage state
				const statePath = path.resolve(
					__dirname,
					"..",
					getAuthStatePath(userKey),
				);
				await context.storageState({ path: statePath });
				console.log(`  Saved auth state to ${statePath}`);
			} catch (error) {
				console.error(`  Failed to authenticate ${userKey}:`, error);
				throw error;
			} finally {
				await context.close();
			}
		}

		console.log("\n=== Global Setup Complete ===\n");
	},
);
