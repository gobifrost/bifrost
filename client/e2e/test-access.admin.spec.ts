/**
 * Test Access — admin happy path against the live access check.
 *
 * A Platform Admin opens a customer user's page, opens Test Access, keeps the
 * user's organization, picks an operation, and reads the trace the API
 * returns: each step, and the report-only outcome.
 */

import { randomUUID } from "node:crypto";

import { test, expect } from "./fixtures/api-fixture";

const SUFFIX = randomUUID().slice(0, 8);
const ORG_NAME = `Contoso ${SUFFIX}`;
const USER_EMAIL = `test-access-${SUFFIX}@e2e.gobifrost.dev`;
const USER_NAME = `Access Tester ${SUFFIX}`;

test.describe("Test Access", () => {
	let organizationId: string;
	let userId: string;

	test.beforeAll(async ({ api }) => {
		const org = await api.post("/api/organizations", {
			data: { name: ORG_NAME, domain: `contoso-${SUFFIX}.gobifrost.dev` },
		});
		expect(org.ok(), `create organization: ${await org.text()}`).toBe(true);
		organizationId = ((await org.json()) as { id: string }).id;

		const user = await api.post("/api/users", {
			data: {
				email: USER_EMAIL,
				name: USER_NAME,
				organization_id: organizationId,
				is_superuser: false,
				invite: false,
			},
		});
		expect(user.ok(), `create user: ${await user.text()}`).toBe(true);
		userId = ((await user.json()) as { id: string }).id;
	});

	test.afterAll(async ({ api }) => {
		const removals = [
			userId && `/api/users/${userId}`,
			organizationId && `/api/organizations/${organizationId}`,
		].filter((path): path is string => !!path);
		for (const path of removals) {
			const deleted = await api.delete(path);
			expect([200, 204, 404]).toContain(deleted.status());
		}
	});

	test("TEST-ACCESS-01 tests a person's access and reads the live trace", async ({
		page,
	}) => {
		await page.goto(`/users/${userId}`);
		await expect(
			page.getByRole("heading", { level: 1, name: USER_NAME }),
		).toBeVisible();

		await page.getByRole("button", { name: "Test Access" }).click();
		const sheet = page.getByRole("dialog", { name: "Test Access" });
		// Prefilled with the person's organization.
		await expect(
			sheet.getByRole("combobox", { name: "Organization" }),
		).toHaveText(ORG_NAME);
		await sheet.getByRole("combobox", { name: "Operation" }).fill("Read");
		await sheet
			.getByRole("option")
			.filter({ hasText: "agents.list" })
			.click();
		await sheet.getByRole("button", { name: "Test Access" }).click();

		const trace = sheet.getByRole("list", { name: "Access Trace" });
		const step = (title: string) =>
			trace.getByRole("listitem").filter({
				has: page.getByRole("heading", { name: title, exact: true }),
			});
		await expect(step("Target in Reach")).toContainText("Passed");
		await expect(step("Permission")).toContainText("Passed");
		await expect(sheet.getByRole("status")).toHaveText("Would be allowed");
	});
});
