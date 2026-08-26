import { expect, test } from "./fixtures/api-fixture";

test("renders structured event criteria authoring at desktop and narrow widths", async ({
	page,
	api,
}) => {
	const name = `Criteria UI ${Date.now()}`;
	const response = await api.post("/api/events/sources", {
		data: {
			name,
			source_type: "webhook",
			webhook: { adapter_name: "generic" },
		},
	});
	expect(response.ok(), await response.text()).toBe(true);
	const source = await response.json();

	try {
		await page.goto(`/event-sources/${source.id}`);
		await page.getByRole("tab", { name: "Subscriptions" }).click();
		await page.getByRole("button", { name: "Add Subscription" }).first().click();
		await expect(page.getByText("All events match this subscription")).toBeVisible();
		await page.getByRole("button", { name: "Add criteria" }).click();
		await expect(page.getByLabel("Criteria field")).toBeVisible();
		await page.screenshot({ path: "test-results/screenshots/event-criteria-desktop.png", fullPage: true });

		await page.evaluate(() => {
			localStorage.setItem("theme", "light");
			document.documentElement.classList.remove("dark");
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await expect(page.getByRole("dialog", { name: "Add Subscription" })).toBeVisible();
		await page.screenshot({ path: "test-results/screenshots/event-criteria-narrow.png", fullPage: true });
	} finally {
		await api.delete(`/api/events/sources/${source.id}`);
	}
});
