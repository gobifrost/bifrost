/**
 * A provider-home reader with managed-customer reach can use integrations
 * without being offered Global or mapping mutations. This uses a fresh user so
 * the scoped role does not mutate the shared org-user fixture used by parallel
 * specs.
 */

import { randomUUID } from "node:crypto";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";
import { loginUserViaAPI, registerAndSetupMFA } from "./setup/auth-helpers";
import type { TestUser, UserCredentials } from "./fixtures/users";

const suffix = randomUUID().slice(0, 8);
const appName = `Reader Home App ${suffix}`;
const appSlug = `reader-home-app-${suffix}`;
const integrationName = `Reader Integration ${suffix}`;
const reader: TestUser = {
	key: `integration-reader-${suffix}`,
	email: `integration-reader-${suffix}@e2e.gobifrost.dev`,
	name: "Integration Reader",
	password: "ReaderPass473!",
	isSuperuser: false,
};

async function expectOk(
	response: Awaited<ReturnType<AuthedApi["get"]>>,
	label: string,
) {
	expect(response.ok(), `${label}: ${await response.text()}`).toBe(true);
}

test.describe("Integration permissions", () => {
	let organizationId = "";
	let homeOrganizationName = "";
	let customerOrganizationId = "";
	let customerOrganizationName = "";
	let platformOperatorRoleId = "";
	let readerId = "";
	let appId = "";
	let integrationId = "";
	let readerCredentials: UserCredentials;

	test.beforeAll(async ({ api }) => {
		const authorization = await api.get("/auth/authorization");
		await expectOk(authorization, "read provider authorization summary");
		organizationId = (
			(await authorization.json()) as { provider_organization_id: string }
		).provider_organization_id;
		const homeOrganization = await api.get(
			`/api/organizations/${organizationId}`,
		);
		await expectOk(homeOrganization, "read provider home organization");
		homeOrganizationName = (
			(await homeOrganization.json()) as { name: string }
		).name;
		const organizations = await api.get("/api/organizations");
		await expectOk(organizations, "list managed customer organizations");
		const customerOrganization = (
			(await organizations.json()) as Array<{
				id: string;
				name: string;
				is_provider: boolean;
			}>
		).find((organization) => !organization.is_provider);
		expect(customerOrganization).toBeDefined();
		customerOrganizationId = customerOrganization!.id;
		customerOrganizationName = customerOrganization!.name;

		const roles = await api.get("/api/roles", {
			params: { include_builtin: true },
		});
		await expectOk(roles, "list builtin roles");
		platformOperatorRoleId =
			((await roles.json()) as Array<{ id: string; name: string }>).find(
				(role) => role.name === "Platform Operator",
			)?.id ?? "";
		expect(platformOperatorRoleId).not.toBe("");

		const user = await api.post("/api/users", {
			data: {
				email: reader.email,
				name: reader.name,
				organization_id: organizationId,
				is_superuser: false,
				invite: false,
			},
		});
		await expectOk(user, "create reader user");
		readerId = ((await user.json()) as { id: string }).id;

		const assignments = await api.get(
			`/api/users/${readerId}/role-assignments`,
		);
		await expectOk(assignments, "read reader role assignments");
		const baseRoleId = (
			(await assignments.json()) as { base_role: { id: string } }
		).base_role.id;
		const placed = await api.put(
			`/api/users/${readerId}/role-assignments`,
			{
				data: {
					base_role_id: baseRoleId,
					additional: [
						{
							role_id: platformOperatorRoleId,
							boundaries: [{ kind: "managed_organizations" }],
						},
					],
				},
			},
		);
		await expectOk(
			placed,
			"place Platform Operator at managed organizations",
		);

		readerCredentials = await registerAndSetupMFA(reader);

		const app = await api.post("/api/applications", {
			data: {
				name: appName,
				slug: appSlug,
				organization_id: organizationId,
				access_level: "authenticated",
				app_model: "standalone_v2",
			},
		});
		await expectOk(app, "create metadata-only V2 app");
		appId = ((await app.json()) as { id: string }).id;

		const integration = await api.post("/api/integrations", {
			data: { name: integrationName },
		});
		await expectOk(integration, "create integration");
		integrationId = ((await integration.json()) as { id: string }).id;
		const mapping = await api.post(
			`/api/integrations/${integrationId}/mappings`,
			{
				data: {
					organization_id: customerOrganizationId,
					entity_id: `reader-home-${suffix}`,
					entity_name: customerOrganizationName,
				},
			},
		);
		await expectOk(mapping, "create managed customer mapping");
	});

	test.afterAll(async ({ api }) => {
		for (const path of [
			integrationId && `/api/integrations/${integrationId}`,
			appId && `/api/applications/${appId}`,
			readerId && `/api/users/${readerId}`,
		].filter((value): value is string => Boolean(value))) {
			const response = await api.delete(path);
			expect([200, 204, 404], `delete ${path}`).toContain(
				response.status(),
			);
		}
	});

	test("shows the home name and read-only integration test scope", async ({
		page,
		context,
	}, testInfo) => {
		const session = await loginUserViaAPI(readerCredentials);
		await page.goto("/");
		const origin = new URL(page.url()).origin;
		await context.addCookies([
			{
				name: "access_token",
				value: session.accessToken,
				url: origin,
				httpOnly: true,
			},
			{
				name: "refresh_token",
				value: session.refreshToken,
				url: origin,
				httpOnly: true,
			},
		]);
		await page.evaluate((accessToken) => {
			localStorage.clear();
			localStorage.setItem("bifrost_access_token", accessToken);
		}, session.accessToken);

		await page.goto("/apps");
		await expect(page.getByText(appName, { exact: true })).toBeVisible();
		await expect(
			page.getByText(homeOrganizationName, { exact: true }),
		).toBeVisible();
		await page.screenshot({
			path: testInfo.outputPath("reader-home-app-list.png"),
			fullPage: true,
			animations: "disabled",
		});

		await page.goto("/integrations");
		await expect(
			page.getByRole("link", { name: integrationName, exact: true }),
		).toBeVisible();
		await expect(
			page.getByRole("button", {
				name: "Create Integration",
				exact: true,
			}),
		).toHaveCount(0);
		await expect(
			page.getByRole("button", {
				name: `${integrationName} actions`,
				exact: true,
			}),
		).toHaveCount(0);

		await page
			.getByRole("link", { name: integrationName, exact: true })
			.click();
		await expect(page).toHaveURL(
			new RegExp(`/integrations/${integrationId}$`),
		);
		await expect(
			page.getByRole("button", { name: "Test Connection", exact: true }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Generate SDK", exact: true }),
		).toHaveCount(0);
		await expect(
			page.getByRole("button", { name: "Edit", exact: true }),
		).toHaveCount(0);
		await expect(
			page.getByRole("button", {
				name: "Configure default values",
				exact: true,
			}),
		).toHaveCount(0);

		await page
			.getByRole("button", { name: "Test Connection", exact: true })
			.click();
		const dialog = page.getByRole("dialog", {
			name: "Test Integration Connection",
			exact: true,
		});
		await expect(dialog).toBeVisible();
		const organizationSelect = dialog.getByRole("combobox", {
			name: "Organization scope",
		});
		await expect(organizationSelect).toContainText(
			customerOrganizationName,
		);
		await organizationSelect.click();
		await expect(
			page.getByRole("option").filter({
				has: page.getByText(customerOrganizationName, { exact: true }),
			}),
		).toBeVisible();
		await expect(page.getByRole("option", { name: /^Global/ })).toHaveCount(
			0,
		);
		await expect(
			page.getByRole("option").filter({
				has: page.getByText(homeOrganizationName, { exact: true }),
			}),
		).toHaveCount(0);
		await page.screenshot({
			path: testInfo.outputPath("reader-integration-test-scope.png"),
			fullPage: true,
			animations: "disabled",
		});
	});
});
