/**
 * Entity Management Acceptance (Admin)
 *
 * Covers the primary browser journey for bulk scope assignment: an admin
 * selects two app resources, opens the edit drawer, reviews the proposed scope
 * changes, applies them, and verifies a third unselected app is unchanged.
 */

import type { Locator, Page } from "./fixtures/api-fixture";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

type Organization = { id: string; name: string };
type Application = {
	id: string;
	slug: string;
	name: string;
	organization_id: string | null;
};

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const ORG_ONE_NAME = `E2E Entity Scope One ${UNIQUE}`;
const ORG_TWO_NAME = `E2E Entity Scope Two ${UNIQUE}`;
const SELECTED_APP_ONE_NAME = `E2E Entity Managed App One ${UNIQUE}`;
const SELECTED_APP_ONE_SLUG = `e2e-entity-managed-one-${UNIQUE}`;
const SELECTED_APP_TWO_NAME = `E2E Entity Managed App Two ${UNIQUE}`;
const SELECTED_APP_TWO_SLUG = `e2e-entity-managed-two-${UNIQUE}`;
const UNSELECTED_APP_NAME = `E2E Entity Untouched App ${UNIQUE}`;
const UNSELECTED_APP_SLUG = `e2e-entity-untouched-${UNIQUE}`;

async function expectOk(
	response: { ok(): boolean; status(): number },
	method: string,
	path: string,
) {
	expect(
		response.ok(),
		`${method} ${path} returned ${response.status()}`,
	).toBe(true);
}

async function createOrganization(
	api: AuthedApi,
	name: string,
	domainPrefix: string,
): Promise<Organization> {
	const path = "/api/organizations";
	const response = await api.post(path, {
		data: {
			name,
			domain: `${domainPrefix}-${UNIQUE}.example`,
		},
	});
	await expectOk(response, "POST", path);
	return (await response.json()) as Organization;
}

async function createApplication(
	api: AuthedApi,
	name: string,
	slug: string,
): Promise<Application> {
	const path = "/api/applications";
	const response = await api.post(path, {
		data: {
			name,
			slug,
			access_level: "authenticated",
			role_ids: [],
			organization_id: null,
			app_model: "inline_v1",
		},
	});
	await expectOk(response, "POST", path);
	return (await response.json()) as Application;
}

async function readApplication(
	api: AuthedApi,
	slug: string,
): Promise<Application> {
	const path = `/api/applications/${slug}`;
	const response = await api.get(path);
	await expectOk(response, "GET", "/api/applications/{slug}");
	return (await response.json()) as Application;
}

async function cleanupApplication(api: AuthedApi, appId: string | undefined) {
	if (!appId) return;
	const response = await api.delete(`/api/applications/${appId}`);
	expect(
		[204, 404],
		`DELETE /api/applications/{app_id} returned ${response.status()}`,
	).toContain(response.status());
}

async function cleanupOrganization(
	api: AuthedApi,
	organizationId: string | undefined,
) {
	if (!organizationId) return;
	const response = await api.delete(`/api/organizations/${organizationId}`);
	expect(
		[204, 404],
		`DELETE /api/organizations/{org_id} returned ${response.status()}`,
	).toContain(response.status());
}

function resourceRow(page: Page, name: string): Locator {
	return page
		.getByRole("list", { name: "Resources" })
		.getByRole("listitem")
		.filter({ hasText: name })
		.first();
}

async function openEntityManagement(page: Page) {
	await page.goto("/entity-management");
	await expect(
		page.getByRole("heading", { name: "Entity Management" }),
	).toBeVisible({ timeout: 10000 });
}

async function filterToSeededApps(page: Page) {
	await expect(
		page.getByRole("checkbox", { name: "Select all visible entities" }),
	).toBeVisible();
	await page.getByRole("textbox", { name: "Search entities" }).fill(UNIQUE);
	await expect(
		page.getByRole("checkbox", { name: `Select ${SELECTED_APP_ONE_NAME}` }),
	).toBeVisible({ timeout: 10000 });
	await expect(
		page.getByRole("checkbox", { name: `Select ${UNSELECTED_APP_NAME}` }),
	).toBeVisible();
}

async function chooseScope(page: Page, organizationName: string) {
	await page
		.getByRole("combobox", { name: "Organization change mode" })
		.click();
	await page.getByRole("option", { name: "Set scope" }).click();
	await page.getByRole("combobox", { name: "Organization scope" }).click();
	await page
		.getByRole("combobox", { name: "Search organizations" })
		.fill(organizationName);
	await page
		.getByRole("option")
		.filter({ has: page.getByText(organizationName, { exact: true }) })
		.click();
}

async function applySelectedScope(
	page: Page,
	organizationName: string,
	previousScope = "Global",
) {
	await page.getByRole("button", { name: "Edit selected" }).click();
	const inspector = page.getByRole("dialog", { name: /Edit 2 resources/ });
	await expect(inspector).toBeVisible();
	await expect(inspector.getByRole("heading", { name: "Review changes" })).toHaveCount(0);
	await chooseScope(page, organizationName);
	await expect(inspector.getByText(SELECTED_APP_ONE_NAME)).toBeVisible();
	await expect(inspector.getByText(SELECTED_APP_TWO_NAME)).toBeVisible();
	await expect(inspector.getByText(UNSELECTED_APP_NAME)).toHaveCount(0);
	const reviewRows = inspector.getByRole("list", { name: "Entities to update" }).getByRole("listitem");
	await expect(reviewRows).toHaveCount(2);
	await expect(reviewRows.first()).toContainText(previousScope);
	await expect(reviewRows.first()).toContainText(organizationName);
	await expect(reviewRows.first()).not.toContainText("Access:");
	await expect(reviewRows.first()).not.toContainText("Roles:");
	await inspector.getByRole("button", { name: "Apply changes" }).click();
	await expect(
		inspector.getByRole("button", { name: "Apply changes" }),
	).toBeDisabled();
	await expect(
		inspector.getByRole("combobox", { name: "Organization change mode" }),
	).toContainText("No change");
	await inspector.getByRole("button", { name: "Close", exact: true }).click();
	await expect(inspector).not.toBeVisible();
	await expect(resourceRow(page, SELECTED_APP_ONE_NAME)).toContainText(
		organizationName,
	);
}

async function expectAppScope(
	api: AuthedApi,
	slug: string,
	organizationId: string | null,
) {
	await expect
		.poll(async () => (await readApplication(api, slug)).organization_id, {
			message: `${slug} organization scope`,
		})
		.toBe(organizationId);
}

test.describe("Entity management acceptance", () => {
	let orgOne: Organization | undefined;
	let orgTwo: Organization | undefined;
	let selectedAppOne: Application | undefined;
	let selectedAppTwo: Application | undefined;
	let unselectedApp: Application | undefined;

	test.beforeAll(async ({ api }) => {
		orgOne = await createOrganization(
			api,
			ORG_ONE_NAME,
			"entity-scope-one",
		);
		orgTwo = await createOrganization(
			api,
			ORG_TWO_NAME,
			"entity-scope-two",
		);
		selectedAppOne = await createApplication(
			api,
			SELECTED_APP_ONE_NAME,
			SELECTED_APP_ONE_SLUG,
		);
		selectedAppTwo = await createApplication(
			api,
			SELECTED_APP_TWO_NAME,
			SELECTED_APP_TWO_SLUG,
		);
		unselectedApp = await createApplication(
			api,
			UNSELECTED_APP_NAME,
			UNSELECTED_APP_SLUG,
		);
	});

	test.afterAll(async ({ api }) => {
		await cleanupApplication(api, selectedAppOne?.id);
		await cleanupApplication(api, selectedAppTwo?.id);
		await cleanupApplication(api, unselectedApp?.id);
		await cleanupOrganization(api, orgOne?.id);
		await cleanupOrganization(api, orgTwo?.id);
	});

	test("bulk assigns two selected entities while preserving an unselected resource", async ({
		page,
		api,
	}) => {
		expect(orgOne).toBeDefined();
		expect(orgTwo).toBeDefined();
		expect(selectedAppOne).toBeDefined();
		expect(selectedAppTwo).toBeDefined();
		expect(unselectedApp).toBeDefined();

		await openEntityManagement(page);
		await filterToSeededApps(page);
		await expect(resourceRow(page, SELECTED_APP_ONE_NAME)).toContainText(
			"Global",
		);
		await page
			.getByRole("checkbox", { name: `Select ${SELECTED_APP_ONE_NAME}` })
			.check();
		await page
			.getByRole("checkbox", { name: `Select ${SELECTED_APP_TWO_NAME}` })
			.check();
		await expect(page.getByText("2 selected").first()).toBeVisible();

		await applySelectedScope(page, ORG_ONE_NAME);
		await expectAppScope(api, SELECTED_APP_ONE_SLUG, orgOne!.id);
		await expectAppScope(api, SELECTED_APP_TWO_SLUG, orgOne!.id);
		await expectAppScope(api, UNSELECTED_APP_SLUG, null);

		await page.reload();
		await filterToSeededApps(page);
		await expect(resourceRow(page, SELECTED_APP_ONE_NAME)).toContainText(
			ORG_ONE_NAME,
		);
		await page
			.getByRole("checkbox", { name: `Select ${SELECTED_APP_ONE_NAME}` })
			.check();
		await page
			.getByRole("checkbox", { name: `Select ${SELECTED_APP_TWO_NAME}` })
			.check();

		await applySelectedScope(page, ORG_TWO_NAME, ORG_ONE_NAME);
		await expectAppScope(api, SELECTED_APP_ONE_SLUG, orgTwo!.id);
		await expectAppScope(api, SELECTED_APP_TWO_SLUG, orgTwo!.id);
		await expectAppScope(api, UNSELECTED_APP_SLUG, null);

		await applySelectedScope(page, "Global", ORG_TWO_NAME);
		await expectAppScope(api, SELECTED_APP_ONE_SLUG, null);
		await expectAppScope(api, SELECTED_APP_TWO_SLUG, null);
		await expectAppScope(api, UNSELECTED_APP_SLUG, null);
	});
});
