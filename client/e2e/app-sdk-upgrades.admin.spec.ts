import { expect, test } from "./fixtures/api-fixture";

const SETTINGS = "/api/maintenance/app-sdk-upgrades/settings";

test("enables automatic App SDK upgrades from Maintenance", async ({ page, api }) => {
	const initial = await api.get(SETTINGS);
	expect(initial.ok()).toBeTruthy();
	const initialSettings = (await initial.json()) as { enabled: boolean };
	expect(initialSettings).toEqual({ enabled: false });

	try {
		await page.goto("/settings/maintenance");

		const card = page
			.locator('[data-slot="card"]')
			.filter({ has: page.getByText("App SDK Upgrades", { exact: true }) });
		await expect(card).toBeVisible();
		const toggle = card.getByRole("switch", {
			name: "Enable Automatic App SDK Upgrades",
		});
		await expect(toggle).toBeEnabled();
		await expect(toggle).not.toBeChecked();

		await toggle.click();
		await expect(toggle).toBeChecked();
		await expect(
			card.getByText(/Progress and failures appear in Notifications/),
		).toBeVisible();
	} finally {
		const restored = await api.put(SETTINGS, {
			data: { enabled: initialSettings.enabled },
		});
		expect(restored.ok()).toBeTruthy();
		expect(await restored.json()).toEqual(initialSettings);
	}
});
