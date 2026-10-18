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
const helperModule = `${workflowName}_helper`;
const helperPath = `${helperModule}.py`;

test.describe("Workflow conversion", () => {
	test.beforeAll(async ({ api }) => {
		const helper = await api.put("/api/files/editor/content", {
			data: {
				path: helperPath,
				encoding: "utf-8",
				content:
					"from bifrost import organizations\n\nasync def load_organizations():\n    return await organizations.list()\n",
			},
		});
		expect(
			helper.ok(),
			`write helper fixture: ${await helper.text()}`,
		).toBe(true);
		const write = await api.put("/api/files/editor/content", {
			data: {
				path: workflowPath,
				encoding: "utf-8",
				content: `from bifrost import workflow\nfrom ${helperModule} import load_organizations\n\n@workflow(name="${workflowName}")\nasync def ${workflowName}() -> list:\n    return await load_organizations()\n`,
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
		for (const path of [workflowPath, helperPath]) {
			const response = await api.delete(
				`/api/files/editor?path=${encodeURIComponent(path)}`,
			);
			expect([200, 204, 404]).toContain(response.status());
		}
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
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-scope-desktop.png"),
			fullPage: true,
		});
		await page
			.getByRole("button", { name: "Analyze selected workflows" })
			.click();
		// Analysis is durable background work; wait for its user-visible result.
		await page.getByRole("heading", { name: "Review proposals" }).waitFor();
		const resultUrl = page.url();
		expect(resultUrl).toContain("job=");
		await page
			.getByRole("button", {
				name: `Review ${workflowName}`,
				exact: true,
			})
			.click();
		await expect(
			page.getByText("organizations.read", { exact: true }).first(),
		).toBeVisible();
		await page.screenshot({
			animations: "disabled",
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
			animations: "disabled",
			path: testInfo.outputPath("conversion-review-mobile.png"),
			fullPage: true,
		});
		await page
			.getByRole("button", {
				name: `Review ${workflowName}`,
				exact: true,
			})
			.click();
		const details = page.getByRole("dialog", {
			name: "Workflow details",
			exact: true,
		});
		await expect(details).toBeVisible();
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-details-mobile.png"),
			fullPage: true,
		});
		await details
			.getByRole("button", { name: "Close", exact: true })
			.click();
		await expect(
			page.getByRole("button", {
				name: `Review ${workflowName}`,
				exact: true,
			}),
		).toBeFocused();
		if (desktopViewport) await page.setViewportSize(desktopViewport);

		await page
			.getByRole("checkbox", { name: `Select ${workflowName}` })
			.check();
		const reviewAction = page.getByRole("button", {
			name: /Review selected/,
		});
		const actionBounds = await reviewAction.boundingBox();
		expect(actionBounds).not.toBeNull();
		expect(actionBounds!.y + actionBounds!.height).toBeLessThanOrEqual(
			page.viewportSize()!.height,
		);
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-selected-desktop.png"),
			fullPage: true,
		});
		await page.getByRole("button", { name: /Review selected/ }).click();
		const confirmation = page.getByRole("alertdialog", {
			name: "Confirm reviewed changes",
		});
		await expect(confirmation).toBeVisible();
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-confirm-desktop.png"),
			fullPage: true,
		});
		await confirmation
			.getByRole("button", {
				name: "Apply reviewed changes",
				exact: true,
			})
			.click();
		await page.getByText(/requested change completed/i).waitFor();
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
			page.getByRole("button", {
				name: `Review ${workflowName}`,
				exact: true,
			}),
		).toBeVisible();
	});
});
