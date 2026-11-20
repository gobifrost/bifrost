/**
 * Organization Tags (Admin)
 *
 * Proves organization tags round-trip through the real UI: bulk-add tags to
 * selected organizations, see them in the Tags column, filter the list by tag,
 * bulk-remove a tag, and filter an integration's mappings by tag.
 *
 * `./test.sh client e2e --screenshots e2e/organization-tags.admin.spec.ts`
 * also saves review screenshots to playwright-results/organization-tags/.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { AuthedApi, Page } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const NAMES = {
	northwind: `Northwind Traders ${UNIQUE}`,
	contoso: `Contoso Dental ${UNIQUE}`,
	fabrikam: `Fabrikam Legal ${UNIQUE}`,
	litware: `Litware Clinic ${UNIQUE}`,
};
const INTEGRATION_NAME = `E2E Tags ${UNIQUE}`;
const SCREENSHOTS = process.env.PLAYWRIGHT_SCREENSHOT_ALL === "1";

type Organization = { id: string; name: string; tags: string[] };

async function createOrganization(api: AuthedApi, name: string) {
	const response = await api.post("/api/organizations", {
		data: { name },
	});
	expect(response.ok(), await response.text()).toBe(true);
	return (await response.json()) as Organization;
}

async function capture(page: Page, name: string) {
	if (!SCREENSHOTS) return;
	await page.screenshot({
		path: `playwright-results/organization-tags/${name}.png`,
	});
}

function orgRow(page: Page, name: string) {
	return page.getByRole("row").filter({ hasText: name });
}

test.describe("Organization tags", () => {
	const orgIds: Record<keyof typeof NAMES, string> = {
		northwind: "",
		contoso: "",
		fabrikam: "",
		litware: "",
	};
	let integrationId = "";

	test.beforeAll(async ({ api }) => {
		for (const key of Object.keys(NAMES) as (keyof typeof NAMES)[]) {
			orgIds[key] = (await createOrganization(api, NAMES[key])).id;
		}
		const tagged = await api.patch(`/api/organizations/${orgIds.litware}`, {
			data: { tags: ["Connectivity"] },
		});
		expect(tagged.ok(), await tagged.text()).toBe(true);

		const integration = await api.post("/api/integrations", {
			data: { name: INTEGRATION_NAME },
		});
		expect(integration.ok(), await integration.text()).toBe(true);
		integrationId = ((await integration.json()) as { id: string }).id;
	});

	test.afterAll(async ({ api }) => {
		if (integrationId) {
			await api.delete(`/api/integrations/${integrationId}`);
		}
		for (const id of Object.values(orgIds)) {
			if (id) await api.delete(`/api/organizations/${id}`);
		}
	});

	test("[ORG-TAGS-01] bulk tag organizations and filter by tag", async ({
		page,
		api,
	}) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto("/organizations");
		await page.getByLabel("Search organizations").fill(UNIQUE);
		await expect(orgRow(page, NAMES.litware)).toContainText("Connectivity");

		await page
			.getByRole("checkbox", { name: `Select ${NAMES.northwind}` })
			.click();
		await page
			.getByRole("checkbox", { name: `Select ${NAMES.contoso}` })
			.click();
		const bar = page.getByRole("region", {
			name: "Bulk organization actions",
		});
		await expect(bar).toContainText("2 selected");
		await bar.getByRole("button", { name: "Add Tags" }).click();

		const dialog = page.getByRole("dialog", { name: "Add Tags" });
		const input = dialog.getByRole("textbox", { name: "Tags to add" });
		await input.fill("MSP");
		await input.press("Enter");
		await input.fill("Licence");
		await input.press("Enter");
		await capture(page, "01-bulk-add-dialog");

		const saved = page.waitForResponse(
			(response) =>
				response.request().method() === "PATCH" &&
				response.url().endsWith("/api/organizations/tags"),
		);
		await dialog.getByRole("button", { name: "Add Tags" }).click();
		expect((await saved).ok()).toBe(true);
		await expect(dialog).toBeHidden();
		await expect(orgRow(page, NAMES.northwind)).toContainText("Licence");
		await expect(orgRow(page, NAMES.contoso)).toContainText("MSP");
		await capture(page, "02-tags-column");

		await page.getByRole("combobox", { name: "Filter by tag" }).click();
		await page.getByRole("option", { name: "Licence" }).click();
		await page.keyboard.press("Escape");
		await expect(page.getByRole("listbox")).toBeHidden();
		await expect(orgRow(page, NAMES.northwind)).toBeVisible();
		await expect(orgRow(page, NAMES.contoso)).toBeVisible();
		await expect(orgRow(page, NAMES.fabrikam)).toBeHidden();
		await expect(orgRow(page, NAMES.litware)).toBeHidden();
		await capture(page, "03-filter-by-tag");

		await page
			.getByRole("checkbox", { name: `Select ${NAMES.contoso}` })
			.click();
		await bar.getByRole("button", { name: "Remove Tags" }).click();
		const removeDialog = page.getByRole("dialog", { name: "Remove Tags" });
		await removeDialog.getByRole("button", { name: "Licence" }).click();
		await capture(page, "04-bulk-remove-dialog");
		await removeDialog.getByRole("button", { name: "Remove Tags" }).click();
		await expect(removeDialog).toBeHidden();
		await expect(orgRow(page, NAMES.contoso)).toBeHidden();

		const listed = await api.get("/api/organizations");
		const tagsById = new Map(
			((await listed.json()) as Organization[]).map((org) => [
				org.id,
				org.tags,
			]),
		);
		expect(tagsById.get(orgIds.northwind)).toEqual(["MSP", "Licence"]);
		expect(tagsById.get(orgIds.contoso)).toEqual(["MSP"]);

		await page.goto(`/integrations/${integrationId}`);
		await page.getByRole("tab", { name: "Mappings" }).click();
		const mappings = page.getByRole("list", {
			name: "Organization mappings",
		});
		await expect(mappings).toBeVisible({ timeout: 10_000 });
		await page
			.getByRole("searchbox", { name: "Search organization mappings" })
			.fill(UNIQUE);
		await page.getByRole("combobox", { name: "Filter by tag" }).click();
		await page.getByRole("option", { name: "MSP" }).click();
		await page.keyboard.press("Escape");
		await expect(page.getByRole("listbox")).toBeHidden();
		const rows = mappings.getByRole("listitem");
		await expect(rows).toHaveCount(2);
		await expect(rows.filter({ hasText: NAMES.northwind })).toBeVisible();
		await expect(rows.filter({ hasText: NAMES.contoso })).toBeVisible();
		await rows.last().scrollIntoViewIfNeeded();
		await capture(page, "05-mappings-filter-by-tag");

		// Search organization tags in the mapping list, then combine with status.
		await page
			.getByRole("combobox", { name: "Filter by tag: MSP", exact: true })
			.click();
		await page.getByRole("option", { name: "Clear Tag Filter" }).click();
		await page.keyboard.press("Escape");
		await page
			.getByRole("searchbox", { name: "Search organization mappings" })
			.fill("Connectivity");
		await expect(rows.filter({ hasText: NAMES.litware })).toBeVisible();
		await expect(rows.filter({ hasText: NAMES.northwind })).toBeHidden();
		await page.getByRole("combobox", { name: "Mapping Status" }).click();
		await page.getByRole("option", { name: /^Mapped\b/ }).click();
		await expect(
			page.getByText("No mappings match your search"),
		).toBeVisible();
		await page.getByRole("combobox", { name: "Mapping Status" }).click();
		await page.getByRole("option", { name: /^Unmapped\b/ }).click();
		await expect(rows.filter({ hasText: NAMES.litware })).toBeVisible();
		await page.setViewportSize({ width: 390, height: 844 });
		await capture(page, "06-mobile-mapping-filters");
	});
});
