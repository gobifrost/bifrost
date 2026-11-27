import { expect, test } from "./fixtures/api-fixture";

test("an admin creates and manages an isolated integration instance", async ({
	page,
	api,
}, testInfo) => {
	const suffix = Date.now();
	const name = `AWS Instances ${suffix}`;
	const orgName = `Instance Customer ${suffix}`;
	let definitionId: string | undefined;
	let orgId: string | undefined;

	try {
		const definition = await api.post("/api/integrations", {
			data: { name, config_schema: [{ key: "api_key", type: "secret", required: true }] },
		});
		expect(definition.ok(), await definition.text()).toBe(true);
		definitionId = ((await definition.json()) as { id: string }).id;
		const org = await api.post("/api/organizations", { data: { name: orgName } });
		expect(org.ok(), await org.text()).toBe(true);
		orgId = ((await org.json()) as { id: string }).id;

		await page.goto(`/integrations/${definitionId}`);
		await page.getByRole("button", { name: "Selected instance: Default" }).click();
		await page.getByRole("menuitem", { name: "Add Instance" }).click();
		const addDialog = page.getByRole("dialog", { name: "Add Instance" });
		await addDialog.getByLabel("Instance Name").fill("PBXs");
		await expect(addDialog.getByLabel("Instance Key")).toHaveValue("pbxs");
		await page.screenshot({ path: testInfo.outputPath("add-instance.png"), animations: "disabled" });
		const created = page.waitForResponse((response) =>
			response.request().method() === "POST" && response.url().endsWith(`/api/integrations/${definitionId}/instances`),
		);
		await addDialog.getByRole("button", { name: "Add Instance", exact: true }).click();
		const createResponse = await created;
		expect(createResponse.ok(), await createResponse.text()).toBe(true);
		const instanceId = ((await createResponse.json()) as { id: string }).id;
		await expect(page).toHaveURL(new RegExp(`instance=${instanceId}`));
		await expect(page.getByRole("button", { name: "Selected instance: PBXs" })).toBeVisible();

		await page.getByRole("button", { name: "Selected instance: PBXs" }).click();
		await expect(page.getByRole("menuitemradio", { name: "Default", exact: true })).toBeVisible();
		await page.screenshot({ path: testInfo.outputPath("instance-selector.png"), animations: "disabled" });
		await page.keyboard.press("Escape");

		const row = page.getByRole("list", { name: "Organization mappings" }).getByRole("listitem").filter({ hasText: orgName });
		const saved = page.waitForResponse((response) =>
			response.request().method() === "POST" && response.url().endsWith(`/api/integrations/${instanceId}/mappings/batch`),
		);
		await row.getByLabel("External entity ID").fill("pbx-account");
		await row.getByLabel("External entity ID").blur();
		const saveResponse = await saved;
		expect(saveResponse.ok(), await saveResponse.text()).toBe(true);
		await page.reload();
		await expect(row.getByLabel("External entity ID")).toHaveValue("pbx-account");
		await page.screenshot({ path: testInfo.outputPath("instance-org-mappings.png"), animations: "disabled" });

		await page.getByRole("button", { name: "Selected instance: PBXs" }).click();
		await page.getByRole("menuitemradio", { name: "Default", exact: true }).click();
		await expect(page.getByRole("button", { name: "Selected instance: Default" })).toBeVisible();
		await expect(row.getByLabel("External entity ID")).toHaveValue("");
		await expect(page.getByRole("button", { name: "Configuration: Needs Configuration" })).toBeVisible();
		await expect(page.getByRole("button", { name: /OAuth:/ })).toHaveCount(0);
		await page.screenshot({ path: testInfo.outputPath("config-only-no-oauth-warning.png"), animations: "disabled" });

		await page.getByRole("button", { name: "Selected instance: Default" }).click();
		await page.getByRole("menuitem", { name: "Manage Instances" }).click();
		await page.getByRole("dialog", { name: "Manage Instances" }).getByRole("button", { name: "Delete PBXs" }).click();
		const removed = page.waitForResponse((response) => response.request().method() === "DELETE" && response.url().endsWith(`/instances/${instanceId}`));
		await page.getByRole("alertdialog").getByRole("button", { name: "Delete Instance" }).click();
		expect((await removed).ok()).toBe(true);
		await expect(page.getByRole("button", { name: "Delete PBXs" })).toHaveCount(0);
	} finally {
		if (definitionId) expect((await api.delete(`/api/integrations/${definitionId}`)).ok()).toBe(true);
		if (orgId) expect((await api.delete(`/api/organizations/${orgId}`)).ok()).toBe(true);
	}
});
