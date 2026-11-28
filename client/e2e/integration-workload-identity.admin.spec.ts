import { expect, test } from "./fixtures/api-fixture";

test("an administrator configures workload identity without OAuth token controls", async ({
	page,
	api,
}) => {
	const name = `Workload Identity ${Date.now()}`;
	let integrationId: string | undefined;
	try {
		const integration = await api.post("/api/integrations", {
			data: { name, authentication_method: "oauth" },
		});
		expect(integration.ok(), await integration.text()).toBe(true);
		integrationId = ((await integration.json()) as { id: string }).id;

		await page.goto(`/integrations/${integrationId}`);
		await page
			.getByRole("button", { name: "Default OAuth: Not Set Up" })
			.click();
		await page.getByRole("button", { name: "Set Up OAuth" }).click();
		await expect(
			page.getByRole("heading", { name: "Configure OAuth for Integration" }),
		).toBeVisible();
		await page.getByRole("combobox").click();
		await page
			.getByRole("option", { name: "Workload Identity" })
			.click();
		await page.getByLabel("Client ID (optional)").fill("runtime-client-id");
		await page.getByLabel("Tenant ID").fill("runtime-tenant-id");
		const helpButton = page.getByRole("button", {
			name: "Authentication is handled by your workflow’s provider SDK using a configured runtime identity. Configure identity trust and permissions externally. Bifrost does not obtain or refresh tokens in this mode.",
		});
		await helpButton.hover();
		await expect(
			page.getByText(
				"Authentication is handled by your workflow’s provider SDK using a configured runtime identity. Configure identity trust and permissions externally. Bifrost does not obtain or refresh tokens in this mode.",
			),
		).toBeVisible();
		const tooltip = page.locator('[data-slot="tooltip-content"]');
		await expect(tooltip).toBeVisible();
		const bounds = await tooltip.boundingBox();
		expect(bounds).not.toBeNull();
		expect(bounds!.width).toBeLessThanOrEqual(320);
		expect(bounds!.height).toBeGreaterThan(40);
		await expect(page.getByLabel(/token url/i)).toHaveCount(0);
		await page.getByRole("button", { name: "Create Connection" }).click();
		await expect(
			page.getByRole("heading", { name: "Configure OAuth for Integration" }),
		).toHaveCount(0);

		const connection = await api.get(
			`/api/oauth/connections/${encodeURIComponent(name)}`,
		);
		expect(connection.ok(), await connection.text()).toBe(true);
		expect(await connection.json()).toMatchObject({
			oauth_flow_type: "workload_identity",
			client_id: "runtime-client-id",
			tenant_id: "runtime-tenant-id",
			token_url: "",
		});

		await expect(
			page.getByRole("button", { name: "Default OAuth: SDK Managed" }),
		).toBeVisible();
		await page
			.getByRole("button", { name: "Default OAuth: SDK Managed" })
			.click();
		await expect(
			page.getByText("Workload Identity", { exact: true }),
		).toBeVisible();
		await expect(page.getByRole("button", { name: "Get Token" })).toHaveCount(0);
		await expect(
			page.getByRole("button", { name: "Connect default" }),
		).toHaveCount(0);
		await expect(
			page.getByRole("button", { name: "Refresh default token" }),
		).toHaveCount(0);
	} finally {
		if (integrationId) {
			await api.delete(`/api/oauth/connections/${encodeURIComponent(name)}`);
			await api.delete(`/api/integrations/${integrationId}`);
		}
	}
});
