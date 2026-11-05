/**
 * Branding Terminology (Admin)
 *
 * Covers: platform admins can rename fixed product nouns and update/reset the
 * primary brand color through the real Branding settings UI. The changed
 * terminology renders before the main UI appears after navigation.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";

type BrandingSettings = {
	application_name?: string | null;
	primary_color?: string | null;
	terminology?: Record<
		"app" | "agent" | "form",
		{ singular?: string | null; plural?: string | null }
	> | null;
	square_logo_url?: string | null;
	rectangle_logo_url?: string | null;
};

const UPDATED_TERMINOLOGY = {
	app: { singular: "Game", plural: "Games" },
	agent: { singular: "Character", plural: "Characters" },
	form: { singular: "Quest", plural: "Quests" },
};
const UPDATED_PRIMARY_COLOR = "#2563eb";

async function readBranding(api: AuthedApi): Promise<BrandingSettings | null> {
	const response = await api.get("/api/branding");
	expect(
		response.ok(),
		`read branding: ${response.status()} ${await response.text()}`,
	).toBe(true);
	return (await response.json()) as BrandingSettings | null;
}

async function restoreBranding(
	api: AuthedApi,
	previous: BrandingSettings | null,
) {
	const response = previous
		? await api.put("/api/branding", {
				data: {
					application_name: previous.application_name ?? null,
					primary_color: previous.primary_color ?? null,
					terminology: previous.terminology ?? null,
				},
			})
		: await api.delete("/api/branding");

	expect(
		[200, 204],
		`restore branding: ${response.status()} ${await response.text()}`,
	).toContain(response.status());
}

async function waitForBrandingUpdate(page: import("./fixtures/api-fixture").Page) {
	const response = await page.waitForResponse(
		(response) =>
			response.request().method() === "PUT" &&
			new URL(response.url()).pathname === "/api/branding",
	);
	expect(
		response.ok(),
		`branding update: ${response.status()} ${await response.text()}`,
	).toBe(true);
}

async function waitForColorReset(page: import("./fixtures/api-fixture").Page) {
	const response = await page.waitForResponse(
		(response) =>
			response.request().method() === "DELETE" &&
			new URL(response.url()).pathname === "/api/branding/color",
	);
	expect(
		response.ok(),
		`branding color reset: ${response.status()} ${await response.text()}`,
	).toBe(true);
}

test.describe("Branding terminology", () => {
	test("updates terminology and primary color through Branding settings", async ({
		api,
		page,
	}) => {
		const previousBranding = await readBranding(api);

		try {
			await page.goto("/settings/branding");
			await expect(
				page.getByText("Product Terminology", { exact: true }),
			).toBeVisible({ timeout: 15000 });

			await page.getByLabel("Color (Hex)").fill(UPDATED_PRIMARY_COLOR);
			const colorUpdate = waitForBrandingUpdate(page);
			await page.getByRole("button", { name: "Update Color" }).click();
			await colorUpdate;

			await page
				.locator("#app-singular")
				.fill(UPDATED_TERMINOLOGY.app.singular);
			await page
				.locator("#app-plural")
				.fill(UPDATED_TERMINOLOGY.app.plural);
			await page
				.locator("#agent-singular")
				.fill(UPDATED_TERMINOLOGY.agent.singular);
			await page
				.locator("#agent-plural")
				.fill(UPDATED_TERMINOLOGY.agent.plural);
			await page
				.locator("#form-singular")
				.fill(UPDATED_TERMINOLOGY.form.singular);
			await page
				.locator("#form-plural")
				.fill(UPDATED_TERMINOLOGY.form.plural);
			const terminologyUpdate = waitForBrandingUpdate(page);
			await page
				.getByRole("button", { name: "Update Terminology" })
				.click();
			await terminologyUpdate;

			await page.reload();
			await expect(page.getByLabel("Color (Hex)")).toHaveValue(
				UPDATED_PRIMARY_COLOR,
			);
			await expect(page.locator("#app-singular")).toHaveValue(
				UPDATED_TERMINOLOGY.app.singular,
			);
			await expect(page.locator("#agent-plural")).toHaveValue(
				UPDATED_TERMINOLOGY.agent.plural,
			);
			await expect(page.locator("#form-plural")).toHaveValue(
				UPDATED_TERMINOLOGY.form.plural,
			);

			const persistedBranding = await readBranding(api);
			expect(persistedBranding).toMatchObject({
				primary_color: UPDATED_PRIMARY_COLOR,
				terminology: UPDATED_TERMINOLOGY,
			});

			await page.goto("/apps");
			await expect(
				page.getByRole("link", { name: "Games" }),
			).toBeVisible();
			await expect(
				page.getByRole("heading", { name: "Games", exact: true }),
			).toBeVisible();

			await page.goto("/agents");
			await expect(
				page.getByRole("link", { name: "Characters" }),
			).toBeVisible();
			await expect(
				page.getByRole("heading", {
					name: "Characters",
					exact: true,
				}),
			).toBeVisible();

			await page.goto("/forms");
			await expect(
				page.getByRole("link", { name: "Quests" }),
			).toBeVisible();
			await expect(
				page.getByRole("heading", { name: "Quests", exact: true }),
			).toBeVisible();

			await page.goto("/settings/branding");
			await expect(page.getByLabel("Color (Hex)")).toHaveValue(
				UPDATED_PRIMARY_COLOR,
			);
			const colorReset = waitForColorReset(page);
			await page
				.getByRole("button", { name: "Reset to default color" })
				.click();
			await colorReset;
			await expect(page.getByLabel("Color (Hex)")).not.toHaveValue(
				UPDATED_PRIMARY_COLOR,
			);
			const resetBranding = await readBranding(api);
			expect(resetBranding?.primary_color ?? null).toBeNull();
			const defaultActionColor = await page.evaluate(() =>
				document.documentElement.classList.contains("dark")
					? "rgb(47, 212, 212)" : "rgb(8, 127, 134)",
			);
			await expect(page.getByRole("button", { name: "Update Color" })).toHaveCSS("background-color", defaultActionColor);

		} finally {
			await restoreBranding(api, previousBranding);
		}
	});
});
