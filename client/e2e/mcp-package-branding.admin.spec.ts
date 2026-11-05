/** MCP Agent Plugin Package Branding (Admin) */
import { expect, test } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";
import type { components } from "../src/lib/v1";

type Branding = components["schemas"]["MCPPackageBranding"];

async function readBranding(api: AuthedApi): Promise<Branding> {
	const response = await api.get("/api/mcp/run/branding");
	expect(response.ok(), await response.text()).toBe(true);
	return (await response.json()) as Branding;
}

test.describe("MCP agent plugin package branding", () => {
	test("saves, uploads, reloads, downloads, and restores package branding", async ({
		page,
		api,
	}, testInfo) => {
		const previous = await readBranding(api);
		const previousLogo = previous.logo_url
			? await api.get("/api/mcp/run/branding/logo")
			: null;
		const previousLogoBytes = previousLogo?.ok()
			? await previousLogo.body()
			: null;
		const previousLogoType =
			previousLogo?.headers()["content-type"] ?? "image/png";
		const name = "Covi Production";
		try {
			await page.setViewportSize({ width: 1440, height: 1100 });
			await page.goto("/settings/mcp");
			await expect(
				page.getByRole("heading", { name: "Agent Plugin Package" }),
			).toBeVisible();
			await page.getByLabel("Package Name").fill(name);
			await page.getByRole("button", { name: "Save Package" }).click();
			await expect(
				page.getByRole("button", { name: "Save Package" }),
			).toBeDisabled();

			const upload = page.getByRole("group", {
				name: "Upload package logo",
			});
			const logo = await page.request.get("/logo.svg");
			expect(logo.ok()).toBe(true);
			await upload.locator('input[type="file"]').setInputFiles({
				name: "package-logo.svg",
				mimeType: "image/svg+xml",
				buffer: await logo.body(),
			});
			await expect(page.getByText("Image updated")).toBeVisible();
			await page.reload();
			await expect(page.getByLabel("Package Name")).toHaveValue(name);
			await expect(
				page.getByText(
					`Package identifier: ${name
						.toLowerCase()
						.replace(/[^a-z0-9]+/g, "-")
						.replace(/^-|-$/g, "")}`,
				),
			).toBeVisible();
			await expect(upload.locator("img")).toHaveCSS("opacity", "1");
			await page.screenshot({
				path: testInfo.outputPath("package-branding.png"),
				fullPage: false,
			});

			await page.setViewportSize({ width: 390, height: 1100 });
			await page.screenshot({
				path: testInfo.outputPath("package-branding-mobile.png"),
				fullPage: false,
			});
			await page.setViewportSize({ width: 1440, height: 1100 });

			const downloadPromise = page.waitForEvent("download");
			await page
				.getByRole("button", { name: "Download Agent Plugin" })
				.click();
			const download = await downloadPromise;
			expect(download.suggestedFilename()).toBe("covi-production.zip");
		} finally {
			const restored = await api.put("/api/mcp/run/branding", {
				data: { name: previous.name },
			});
			expect(restored.ok(), await restored.text()).toBe(true);
			if (previousLogoBytes) {
				const restoredLogo = await api.post(
					"/api/mcp/run/branding/logo",
					{
						multipart: {
							file: {
								name: "previous-package-logo.png",
								mimeType: previousLogoType,
								buffer: previousLogoBytes,
							},
						},
					},
				);
				expect(restoredLogo.ok(), await restoredLogo.text()).toBe(true);
			} else {
				const removed = await api.delete("/api/mcp/run/branding/logo");
				expect([200, 204, 404]).toContain(removed.status());
			}
		}
	});
});
