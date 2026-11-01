/**
 * Integration Mapping Acceptance (Admin)
 *
 * Proves organization mappings persist through the real integration detail UI.
 * The fixture seeds one integration, two organizations, and one existing
 * mapping through the API. The browser edits that mapping, adds another, reloads
 * the page, searches the mapping list, and verifies persisted records from
 * GET /api/integrations/{id}.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";
import type { Locator, Page } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const INTEGRATION_NAME = `E2E Mapping ${UNIQUE}`;
const FIRST_ORG_NAME = `Mapping First ${UNIQUE}`;
const SECOND_ORG_NAME = `Mapping Second ${UNIQUE}`;
const ORIGINAL_ENTITY = `tenant-original-${UNIQUE}`;
const UPDATED_ENTITY = `tenant-updated-${UNIQUE}`;
const CREATED_ENTITY = `tenant-created-${UNIQUE}`;

type Organization = { id: string; name: string };
type Mapping = {
	id: string;
	organization_id: string | null;
	entity_id: string;
	entity_name?: string | null;
};
type IntegrationDetail = {
	id: string;
	mappings: Mapping[];
};

async function createOrganization(api: AuthedApi, name: string) {
	const response = await api.post("/api/organizations", {
		data: {
			name,
			domain: `${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.gobifrost.dev`,
		},
	});
	expect(response.ok(), await response.text()).toBe(true);
	return (await response.json()) as Organization;
}

async function readIntegration(api: AuthedApi, integrationId: string) {
	const response = await api.get(`/api/integrations/${integrationId}`);
	expect(response.ok(), await response.text()).toBe(true);
	return (await response.json()) as IntegrationDetail;
}

function mappingForOrg(detail: IntegrationDetail, orgId: string) {
	return detail.mappings.find((mapping) => mapping.organization_id === orgId);
}

function mappingRow(page: Page, orgName: string) {
	return page
		.getByRole("list", { name: "Organization mappings" })
		.getByRole("listitem")
		.filter({ hasText: orgName });
}

async function setManualEntityId(
	page: Page,
	row: Locator,
	integrationId: string,
	entityId: string,
) {
	const batchRequest = page.waitForResponse(
		(response) =>
			response.request().method() === "POST" &&
			response
				.url()
				.endsWith(`/api/integrations/${integrationId}/mappings/batch`),
	);
	await row.getByLabel("External entity ID").fill(entityId);
	await row.getByLabel("External entity ID").blur();
	const response = await batchRequest;
	expect(response.ok(), await response.text()).toBe(true);
}

test.describe("Integration mapping acceptance", () => {
	let integrationId = "";
	let firstOrgId = "";
	let secondOrgId = "";

	test.beforeAll(async ({ api }) => {
		const firstOrg = await createOrganization(api, FIRST_ORG_NAME);
		const secondOrg = await createOrganization(api, SECOND_ORG_NAME);
		firstOrgId = firstOrg.id;
		secondOrgId = secondOrg.id;

		const integration = await api.post("/api/integrations", {
			data: { name: INTEGRATION_NAME },
		});
		expect(integration.ok(), await integration.text()).toBe(true);
		integrationId = ((await integration.json()) as { id: string }).id;

		const mapping = await api.post(
			`/api/integrations/${integrationId}/mappings`,
			{
				data: {
					organization_id: firstOrgId,
					entity_id: ORIGINAL_ENTITY,
					entity_name: ORIGINAL_ENTITY,
				},
			},
		);
		expect(mapping.ok(), await mapping.text()).toBe(true);
	});

	test.afterAll(async ({ api }) => {
		if (integrationId) {
			expect([200, 204, 404]).toContain(
				(
					await api.delete(`/api/integrations/${integrationId}`)
				).status(),
			);
		}
		if (firstOrgId) {
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/organizations/${firstOrgId}`)).status(),
			);
		}
		if (secondOrgId) {
			expect([200, 204, 404]).toContain(
				(
					await api.delete(`/api/organizations/${secondOrgId}`)
				).status(),
			);
		}
	});

	test("[MAPPING-01] add and edit organization mappings persist after reload and search", async ({
		page,
		api,
	}) => {
		await page.goto(`/integrations/${integrationId}`);
		await page.getByRole("tab", { name: "Mappings" }).click();
		await expect(
			page.getByRole("list", { name: "Organization mappings" }),
		).toBeVisible({ timeout: 10_000 });

		await setManualEntityId(
			page,
			mappingRow(page, FIRST_ORG_NAME),
			integrationId,
			UPDATED_ENTITY,
		);
		await setManualEntityId(
			page,
			mappingRow(page, SECOND_ORG_NAME),
			integrationId,
			CREATED_ENTITY,
		);

		await page.reload();
		await page.getByRole("tab", { name: "Mappings" }).click();
		await expect(
			mappingRow(page, FIRST_ORG_NAME).getByLabel("External entity ID"),
		).toHaveValue(UPDATED_ENTITY, { timeout: 10_000 });
		await expect(
			mappingRow(page, SECOND_ORG_NAME).getByLabel("External entity ID"),
		).toHaveValue(CREATED_ENTITY);

		await page
			.getByRole("searchbox", { name: "Search organization mappings" })
			.fill(CREATED_ENTITY);
		await expect(
			page
				.getByRole("list", { name: "Organization mappings" })
				.getByRole("listitem"),
		).toHaveCount(1);
		await expect(mappingRow(page, SECOND_ORG_NAME)).toBeVisible();
		await expect(mappingRow(page, FIRST_ORG_NAME)).toBeHidden();

		const detail = await readIntegration(api, integrationId);
		expect(mappingForOrg(detail, firstOrgId)).toMatchObject({
			entity_id: UPDATED_ENTITY,
			entity_name: UPDATED_ENTITY,
		});
		expect(mappingForOrg(detail, secondOrgId)).toMatchObject({
			entity_id: CREATED_ENTITY,
			entity_name: CREATED_ENTITY,
		});
		expect(detail.mappings).toHaveLength(2);
	});
});
