import { expect, test } from "./fixtures/api-fixture";

test("an admin sees the external capacity card on worker settings", async ({
	page,
	api,
}) => {
	const response = await api.get("/api/admin/kubernetes/capacity");
	expect(response.ok()).toBeTruthy();
	const status = (await response.json()) as { configured: boolean };
	await page.goto("/settings/ai");
	await page.getByRole("button", { name: "Workers", exact: true }).click();
	await page
		.getByRole("button", { name: "Scaling & Isolation", exact: true })
		.click();
	await expect(page.getByText("External Capacity")).toBeVisible();
	if (status.configured) {
		await expect(page.getByText("Worker Memory")).toBeVisible();
	} else {
		await expect(
			page.getByText("No external capacity provider is configured."),
		).toBeVisible();
	}
});
