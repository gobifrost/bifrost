import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";
import {
	authenticateInBrowser,
	registerAndSetupMFA,
} from "./setup/auth-helpers";
import type { TestUser } from "./fixtures/users";
import type { components } from "../src/lib/v1";

// A separate administrator keeps this grant out of the shared auth fixture.
// No worker jobs are needed: this journey proves the browser-to-API assignment.
test("a Platform Admin explicitly assigns themselves Secrets Reader", async ({
	api,
	browser,
}) => {
	const suffix = randomUUID().slice(0, 8);
	const person: TestUser = {
		key: `self-assignment-${suffix}`,
		email: `self-assignment-${suffix}@e2e.gobifrost.dev`,
		name: "Self Assignment Admin",
		password: `SelfAssignment-${suffix}!Aa1`,
		isSuperuser: true,
	};
	const authorization = await api.get("/auth/authorization");
	expect(authorization.ok()).toBe(true);
	const { provider_organization_id: organizationId } =
		await authorization.json();
	const created = await api.post("/api/users", {
		data: {
			email: person.email,
			name: person.name,
			organization_id: organizationId,
			is_superuser: true,
			invite: false,
		},
	});
	expect(created.ok(), await created.text()).toBe(true);
	const { id: userId } = await created.json();
	const ownContext = await browser.newContext({
		baseURL: process.env.TEST_BASE_URL || "http://localhost:3000",
		storageState: { cookies: [], origins: [] },
	});
	try {
		const credentials = await registerAndSetupMFA(person);
		// This journey starts after onboarding; announcements must not cover the role editor.
		const headers = { Authorization: `Bearer ${credentials.accessToken}` };
		const feed = await ownContext.request.get("/api/product-updates", {
			headers,
		});
		expect(feed.ok()).toBe(true);
		const updates =
			(await feed.json()) as components["schemas"]["ProductUpdatesFeedResponse"];
		const receipt = await ownContext.request.post(
			"/api/product-updates/receipts",
			{
				headers,
				data: {
					entry_ids: updates.bundle.entries
						.filter((entry) => entry.in_app !== false)
						.map((entry) => entry.id),
				},
			},
		);
		expect(receipt.ok()).toBe(true);
		const page = await ownContext.newPage();
		await authenticateInBrowser(page, credentials);
		await page.goto(`/users/${userId}`);
		const roles = page.getByRole("region", { name: "Additional Roles" });
		await expect(
			roles.getByRole("button", { name: "Remove Platform Admin" }),
		).toBeVisible();
		await expect(
			roles.getByRole("button", { name: "Remove Secrets Reader" }),
		).toHaveCount(0);
		await page
			.getByRole("combobox", { name: "Select additional roles..." })
			.click();
		await page
			.getByRole("listbox")
			.getByRole("option", { name: /Secrets Reader/ })
			.click();
		await page.keyboard.press("Escape");
		await expect(page.getByText("Applies everywhere.")).toBeVisible();
		await page.getByRole("button", { name: "Save Roles" }).click();
		await expect(page.getByText("Roles saved")).toBeVisible();
		await page.reload();
		await expect(
			roles.getByRole("button", { name: "Remove Secrets Reader" }),
		).toBeVisible();
		await expect(
			roles.getByRole("button", { name: "Remove Platform Admin" }),
		).toBeVisible();
	} finally {
		await ownContext.close();
		const deleted = await api.delete(`/api/users/${userId}`);
		expect([204, 404]).toContain(deleted.status());
	}
});
