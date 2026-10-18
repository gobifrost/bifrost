/**
 * Workflow conversion — platform-admin happy path.
 *
 * This uses a real synthetic workflow, durable PlatformJob execution, and
 * browser WebSocket progress. It deliberately does not mock conversion APIs.
 */
import { randomUUID } from "node:crypto";

import { test, expect } from "./fixtures/api-fixture";

const suffix = randomUUID().replaceAll("-", "").slice(0, 12);
const workflowName = `synthetic_conversion_${suffix}`;
const workflowPath = `${workflowName}.py`;

test.describe("Workflow conversion", () => {
	test.beforeAll(async ({ api }) => {
		const write = await api.put("/api/files/editor/content", {
			data: {
				path: workflowPath,
				encoding: "utf-8",
				content: `from bifrost import workflow\n\n@workflow(name="${workflowName}")\nasync def ${workflowName}() -> dict:\n    return {"fixture": "synthetic"}\n`,
			},
		});
		expect(
			write.ok(),
			`write conversion fixture: ${await write.text()}`,
		).toBe(true);
		const register = await api.post("/api/workflows/register", {
			data: { path: workflowPath, function_name: workflowName },
		});
		expect(
			register.ok(),
			`register conversion fixture: ${await register.text()}`,
		).toBe(true);
	});

	test.afterAll(async ({ api }) => {
		const response = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(workflowPath)}`,
		);
		expect([200, 204, 404]).toContain(response.status());
	});

	test("WORKFLOW-CONVERSION-01 analyzes, reviews, applies and reopens a synthetic workflow", async ({
		page,
	}, testInfo) => {
		await page.goto("/workflows");
		await page
			.getByPlaceholder("Search by name, description, or category...")
			.fill(workflowName);
		await page
			.getByRole("button", {
				name: `${workflowName} actions`,
				exact: true,
			})
			.click();
		await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
		const dialog = page.getByRole("dialog", {
			name: "Edit Workflow Settings",
		});
		await dialog.getByRole("tab", { name: "Access", exact: true }).click();
		await dialog
			.getByRole("link", { name: "Review conversion proposal" })
			.click();

		await expect(page).toHaveURL(/\/workflows\/convert\?workflows=/);
		await page
			.getByRole("button", { name: "Analyze selected workflows" })
			.click();
		await expect(
			page.getByRole("heading", { name: "Review proposals" }),
		).toBeVisible();
		const resultUrl = page.url();
		expect(resultUrl).toContain("job=");
		await page.screenshot({
			path: testInfo.outputPath("conversion-review-desktop.png"),
			fullPage: true,
		});
		const desktopViewport = page.viewportSize();
		await page.setViewportSize({ width: 390, height: 844 });
		await expect(
			page.getByRole("heading", { name: "Review proposals" }),
		).toBeVisible();
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= window.innerWidth,
			),
		).toBe(true);
		await page.screenshot({
			path: testInfo.outputPath("conversion-review-mobile.png"),
			fullPage: true,
		});
		if (desktopViewport) await page.setViewportSize(desktopViewport);

		await page
			.getByRole("checkbox", { name: `Select ${workflowName}` })
			.check();
		await page
			.getByRole("button", { name: "Apply 1 reviewed proposal" })
			.click();
		await expect(
			page.getByRole("heading", { name: "Apply and verify" }),
		).toBeVisible();
		await expect(
			page.getByText(/requested change completed/i),
		).toBeVisible();
		await page.getByRole("button", { name: "Return to review" }).click();
		await expect(
			page.getByRole("heading", { name: "Review proposals" }),
		).toBeVisible();

		await page.goto(resultUrl);
		await expect(
			page.getByRole("heading", { name: "Review proposals" }),
		).toBeVisible();
		await expect(
			page.getByText(workflowName, { exact: true }),
		).toBeVisible();
	});
});
