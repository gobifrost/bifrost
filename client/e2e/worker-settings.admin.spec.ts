import { expect, test } from "./fixtures/api-fixture";

test("an admin saves worker scaling outside Kubernetes", async ({ page, api }) => {
	const status = await api.get("/api/admin/kubernetes/status");
	expect(status.ok()).toBeTruthy();
	expect((await status.json()).configured).toBe(false);
	const response = await api.get("/api/admin/kubernetes/execution");
	expect(response.ok()).toBeTruthy();
	const original = (await response.json()).policy.idle_seconds as number;
	const changed = original === 86400 ? original - 1 : original + 1;
	try {
		await page.goto("/settings/ai");
		await page.getByRole("button", { name: "Workers", exact: true }).click();
		await page.getByRole("button", { name: "Scaling & Isolation", exact: true }).click();
		await expect(page.getByLabel("Max Workers", { exact: true })).toBeEnabled();
		await expect(page.getByLabel("Isolate Build Jobs")).toBeDisabled();
		await expect(page.getByLabel("Max Parallel Runs").first()).toBeEnabled();
		await page.getByLabel("Idle Time (Seconds)").fill(String(changed));
		const [saved] = await Promise.all([
			page.waitForResponse((result) =>
				result.url().endsWith("/api/admin/kubernetes/policy") && result.request().method() === "PUT",
			),
			page.getByRole("button", { name: "Save Scaling", exact: true }).click(),
		]);
		expect(saved.ok()).toBeTruthy();
		expect((await saved.json()).policy.idle_seconds).toBe(changed);
		await expect(page.getByRole("button", { name: "Save Scaling", exact: true })).toBeDisabled();
	} finally {
		const restored = await api.put("/api/admin/kubernetes/policy", {
			data: { idle_seconds: original },
		});
		expect(restored.ok()).toBeTruthy();
	}
});
