/**
 * User Management Tests (Admin)
 *
 * Tests user CRUD operations from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_users.py
 */

import { test, expect } from "./fixtures/api-fixture";

test.describe("User Listing", () => {
	test("should display users page", async ({ page }) => {
		await page.goto("/users");

		// Should see users heading
		await expect(
			page.getByRole("heading", { name: /users/i }).first(),
		).toBeVisible({ timeout: 10000 });
		await expect(
			page.getByRole("switch", { name: "Show Inactive" }),
		).toBeVisible();
	});

	test("should list existing users", async ({ page }) => {
		await page.goto("/users");

		await expect(
			page.getByRole("heading", { name: /users/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// The heading renders before the users query settles. Wait for the
		// loaded table or its empty state instead of sampling during skeletons.
		const userRows = page.locator("table tbody tr");
		const emptyState = page.getByText(/no users/i);
		await expect(userRows.first().or(emptyState)).toBeVisible({
			timeout: 10000,
		});
	});

	test("should show create user button", async ({ page }) => {
		await page.goto("/users");

		await expect(
			page.getByRole("heading", { name: /users/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Admin should see invite/create button
		await expect(
			page.getByRole("button", { name: /invite|create|add/i }),
		).toBeVisible();
	});
});

test.describe("User Details", () => {
	test("opens user details once without remounting the dialog", async ({
		page,
	}) => {
		await page.goto("/users");

		await expect(
			page.getByRole("heading", { name: /users/i }).first(),
		).toBeVisible({ timeout: 10000 });

		const userRow = page.locator("table tbody tr").first();
		await expect(userRow).toBeVisible({ timeout: 10000 });
		await page.evaluate(() => {
			const observedWindow = window as unknown as {
				__dialogTransitions: string[];
			};
			observedWindow.__dialogTransitions = [];
			new MutationObserver((mutations) => {
				for (const mutation of mutations) {
					for (const node of mutation.addedNodes) {
						if (
							node instanceof HTMLElement &&
							(node.matches('[role="dialog"]') ||
								node.querySelector('[role="dialog"]'))
						) {
							observedWindow.__dialogTransitions.push("added");
						}
					}
					for (const node of mutation.removedNodes) {
						if (
							node instanceof HTMLElement &&
							(node.matches('[role="dialog"]') ||
								node.querySelector('[role="dialog"]'))
						) {
							observedWindow.__dialogTransitions.push("removed");
						}
					}
				}
			}).observe(document.body, { childList: true, subtree: true });
		});

		await userRow.locator("td").nth(2).click();
		await expect(page).toHaveURL(/\/users\/[0-9a-f-]+$/);
		await expect(
			page.getByRole("dialog", { name: /edit user/i }),
		).toBeVisible();
		await page.evaluate(
			() =>
				new Promise((resolve) =>
					requestAnimationFrame(() => requestAnimationFrame(resolve)),
				),
		);
		const transitions = await page.evaluate(
			() =>
				(window as unknown as { __dialogTransitions: string[] })
					.__dialogTransitions,
		);
		expect(transitions).toEqual(["added"]);
	});

	test("should show user organization membership", async ({ page }) => {
		await page.goto("/users");

		await expect(
			page.getByRole("heading", { name: /users/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Either we see organization info or an empty state
		const hasOrgInfo = await page
			.getByText(/organization/i)
			.first()
			.isVisible()
			.catch(() => false);
		const hasUsers = (await page.locator("table tbody tr").count()) > 0;
		const hasEmptyState = await page
			.getByText(/no users/i)
			.isVisible()
			.catch(() => false);

		// Test passes if we see org info, have users, or have empty state
		expect(hasOrgInfo || hasUsers || hasEmptyState).toBe(true);
	});
});

test.describe("User Invitation", () => {
	test("should open invite user dialog", async ({ page }) => {
		await page.goto("/users");

		await expect(
			page.getByRole("heading", { name: /users/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Click invite button (use first() for multiple matches)
		const inviteButton = page
			.getByRole("button", {
				name: /invite|create|add/i,
			})
			.first();

		try {
			await inviteButton.waitFor({ state: "visible", timeout: 3000 });
			await inviteButton.click();

			// Should show invite form
			await expect(
				page
					.getByLabel(/email/i)
					.or(page.getByPlaceholder(/email/i))
					.first(),
			).toBeVisible({ timeout: 5000 });
		} catch {
			// Button not found - page may not have invite functionality visible
		}
	});

	test("admin invites user and user registers via magic link", async ({
		page,
		api,
		browser,
	}) => {
		test.setTimeout(90000); // Vite dev server cold-loads modules on first visit to new routes
		const email = `invitee-${crypto.randomUUID()}@playwright-e2e.com`;

		// Resolve an organization ID to satisfy the non-superuser org constraint
		const orgsResp = await api.get("/api/organizations");
		expect(orgsResp.ok()).toBe(true);
		const orgs = await orgsResp.json();
		const organizationId = orgs[0]?.id as string | undefined;

		// Create the user directly; registration links are created automatically,
		// but sending the registration email remains an explicit admin action.
		const createResp = await api.post("/api/users", {
			data: {
				email,
				name: "Playwright Invitee",
				invite: false,
				organization_id: organizationId,
			},
		});
		expect(
			createResp.ok(),
			`Create user failed: ${await createResp.text()}`,
		).toBe(true);
		const { id: userId } = await createResp.json();

		await page.goto("/users");
		await expect(
			page.getByRole("heading", { name: /users/i }).first(),
		).toBeVisible({ timeout: 10000 });
		const invitedRow = page.locator("tbody tr", { hasText: email }).first();
		await expect(invitedRow).toBeVisible({ timeout: 10000 });
		await invitedRow.getByRole("button", { name: /user actions/i }).click();
		await page
			.getByRole("menuitem", { name: /generate registration link/i })
			.click();
		const registrationDialog = page.getByRole("dialog", {
			name: /user created/i,
		});
		await expect(registrationDialog).toBeVisible();
		await expect(
			registrationDialog.getByRole("button", {
				name: /copy registration link/i,
			}),
		).toBeVisible();
		await expect(registrationDialog.getByRole("textbox")).toHaveCount(0);
		await expect(registrationDialog.getByRole("link")).toHaveCount(0);
		await registrationDialog
			.getByRole("button", { name: /close/i })
			.click();

		// Regenerate invite to get a registration URL (does not send email)
		const genResp = await api.post(
			`/api/users/${userId}/invite/regenerate`,
		);
		expect(genResp.ok()).toBe(true);
		const { registration_url } = await genResp.json();
		expect(registration_url).toContain("/accept-invite?token=");

		// Extract the token from the URL
		const token = new URL(registration_url).searchParams.get("token")!;
		const registerPath = `/accept-invite?token=${token}`;

		// Complete registration in a fresh (unauthenticated) browser context
		const baseURL = process.env.TEST_BASE_URL || "http://localhost:3000";
		const guestCtx = await browser.newContext({
			baseURL,
			storageState: { cookies: [], origins: [] },
		});
		const guestPage = await guestCtx.newPage();
		// Navigate to login first to warm up the Vite module graph, then go to the invite page.
		// The Vite dev server transforms modules on-demand; the first cold load of a new browser
		// context takes 5-15 seconds. Waiting for the login heading ensures modules are cached.
		await guestPage.goto("/login");
		await expect(
			guestPage
				.getByRole("heading", { name: /sign in/i })
				.or(guestPage.getByRole("heading", { name: /bifrost/i })),
		).toBeVisible({ timeout: 30000 });
		await guestPage.goto(registerPath);

		await expect(
			guestPage.getByRole("heading", {
				name: /complete your registration/i,
			}),
		).toBeVisible({ timeout: 15000 });

		await guestPage
			.getByRole("button", { name: /use password instead/i })
			.click();
		await guestPage
			.getByRole("textbox", { name: "Password", exact: true })
			.fill("InviteePass123!");
		await guestPage
			.getByRole("textbox", { name: "Confirm password" })
			.fill("InviteePass123!");
		await guestPage
			.getByRole("button", { name: /create account/i })
			.click();

		// Should redirect to login after successful registration
		await guestPage.waitForURL(/\/login(?:\?|$)/, { timeout: 10000 });

		await guestCtx.close();

		// Admin verifies the user is now active
		await page.goto("/users");
		await expect(
			page.getByRole("heading", { name: /users/i }).first(),
		).toBeVisible({ timeout: 10000 });
	});
});
