import type { components } from "@/lib/v1";
import { test, expect } from "@playwright/test";

// Isolated spike stack with harmless workflow and integration fixtures.
test("integration links retain labels in the menu and pinned buttons launch the actual workflow", async ({
	page,
}) => {
	await page.goto("/login");
	await page.getByLabel("Email").fill(process.env.BIFROST_PREVIEW_EMAIL!);
	await page
		.getByLabel("Password", { exact: true })
		.fill(process.env.BIFROST_PREVIEW_PASSWORD!);
	const feedResponse = page.waitForResponse(
		(response) =>
			response.url().endsWith("/api/product-updates") &&
			response.request().method() === "GET",
	);
	await page.getByRole("button", { name: "Sign In", exact: true }).click();
	await page.waitForURL((url) => !url.pathname.startsWith("/login"));
	const feed: components["schemas"]["ProductUpdatesFeedResponse"] = await (
		await feedResponse
	).json();
	if (
		feed.bundle.entries.some(
			(entry) =>
				entry.in_app !== false &&
				!feed.seen_entry_ids.includes(entry.id),
		)
	) {
		const updates = page.getByRole("dialog", { name: "What's New" });
		await expect(updates).toBeVisible();
		await updates
			.getByRole("button", { name: "Done", exact: true })
			.click();
	}
	await page.goto(
		`/integrations/${process.env.BIFROST_PREVIEW_INTEGRATION_ID}`,
	);
	const actions = page.getByRole("group", { name: "Integration workflows" });
	await actions
		.getByRole("button", { name: "Workflows", exact: true })
		.click();
	await page
		.getByRole("menuitem", { name: "Manage Linked Workflows" })
		.click();
	await page.getByRole("combobox", { name: "Workflow", exact: true }).click();
	await page
		.getByRole("option", { name: "Sync Customers", exact: true })
		.click();
	await page.getByRole("textbox", { name: "Button Label" }).fill("Sync Now");
	await page
		.getByRole("button", { name: "Link Workflow", exact: true })
		.click();
	await page.getByRole("checkbox", { name: "Pin as Top Button" }).check();
	await page.getByRole("combobox", { name: "Workflow", exact: true }).click();
	await page
		.getByRole("option", {
			name: "Verify Integration Settings",
			exact: true,
		})
		.click();
	await page
		.getByRole("textbox", { name: "Button Label" })
		.fill("Verify Settings");
	await page
		.getByRole("button", { name: "Link Workflow", exact: true })
		.click();
	await page
		.getByRole("checkbox", { name: "Pin as Top Button" })
		.nth(1)
		.check();
	await page.setViewportSize({ width: 1920, height: 1000 });
	await page.getByRole("button", { name: "Done", exact: true }).click();
	await expect(
		actions.getByRole("button", { name: "Verify Settings", exact: true }),
	).toBeVisible();
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(
		actions.getByRole("button", { name: "Sync Now", exact: true }),
	).toBeVisible();
	await expect(
		actions.getByRole("button", { name: "Verify Settings", exact: true }),
	).toHaveCount(0);
	const first = await actions
		.getByRole("button", { name: "Sync Now", exact: true })
		.boundingBox();
	const menu = await actions
		.getByRole("button", { name: /Workflows/ })
		.boundingBox();
	expect(first?.y).toBe(menu?.y);
	await actions.getByRole("button", { name: /Workflows/ }).click();
	await expect(
		page.getByRole("menuitem", { name: /Sync Now/ }),
	).toBeVisible();
	await page.getByRole("menuitem", { name: /Verify Settings/ }).click();
	await expect(page.getByRole("dialog")).toContainText(
		"Verify Integration Settings",
	);
	await page
		.getByRole("dialog")
		.getByRole("button", { name: "Close", exact: true })
		.click();
	await actions
		.getByRole("button", { name: "Sync Now", exact: true })
		.click();
	const accepted = page.waitForResponse(
		(r) =>
			r.url().endsWith("/api/workflows/execute") &&
			r.request().method() === "POST",
	);
	await page
		.getByRole("button", { name: "Run Workflow", exact: true })
		.click();
	const run = await (await accepted).json();
	await expect(
		page.getByRole("link", { name: "Open execution in new tab" }),
	).toHaveAttribute("href", `/history/${run.execution_id}`);
	await expect(page.getByRole("dialog")).toContainText(/Running|Completed/);
});
