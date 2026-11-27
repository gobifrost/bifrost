import type { components } from "@/lib/v1";
import { test, expect } from "@playwright/test";

// Read-only global controls against the isolated development stack.
test("account menu keeps secondary header controls reachable on desktop and phone", async ({
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

	await page.goto("/agents");
	await expect(
		page.getByRole("heading", { name: "Agents", exact: true }),
	).toBeVisible();
	await page.emulateMedia({ reducedMotion: "reduce" });
	for (const width of [1440, 390]) {
		await page.setViewportSize({ width, height: 844 });
		const header = page.getByRole("banner");
		await expect(
			header.getByRole("button", { name: "Account menu" }),
		).toBeVisible();
		await expect(
			header.getByRole("button", { name: /Search/ }),
		).toBeVisible();
		await expect(
			header.getByRole("button", {
				name: /Connect AI|Presentation Mode|Toggle theme|Shell/,
			}),
		).toHaveCount(0);
		expect(await header.boundingBox()).toMatchObject({ height: 64 });
		await page.getByRole("button", { name: "Account menu" }).click();
		await page
			.getByRole("menuitem", { name: "Appearance", exact: true })
			.click();
		const dark = page.getByRole("menuitemcheckbox", { name: "Dark Theme" });
		const wasDark = (await dark.getAttribute("aria-checked")) === "true";

		await dark.click();
		await expect(page.locator("html")).toHaveClass(
			wasDark ? /^(?!.*dark).*$/ : /dark/,
		);
		await page.getByRole("button", { name: "Account menu" }).click();
		await page
			.getByRole("menuitem", { name: "Appearance", exact: true })
			.click();
		const presentation = page.getByRole("menuitemcheckbox", {
			name: "Presentation Mode",
		});
		const wasPresenting =
			(await presentation.getAttribute("aria-checked")) === "true";
		await presentation.click();
		if (wasPresenting)
			await expect(page.locator("html")).not.toHaveAttribute(
				"data-presentation-mode",
				"on",
			);
		else
			await expect(page.locator("html")).toHaveAttribute(
				"data-presentation-mode",
				"on",
			);
		await page.getByRole("button", { name: "Account menu" }).click();
		await page.getByRole("menuitem", { name: "Help", exact: true }).click();
		await expect(
			page.getByRole("menuitem", { name: "Documentation" }),
		).toHaveAttribute("href", "https://gobifrost.com/docs/");
		await expect(
			page.getByRole("menuitem", { name: "Release Notes" }),
		).toHaveAttribute("href", "/whats-new");
		await page.keyboard.press("Escape");
		await page.keyboard.press("Escape");
		await page.getByRole("button", { name: "Account menu" }).click();
		await page
			.getByRole("menuitem", { name: "Connect AI Assistants" })
			.click();
		const dialog = page.getByRole("dialog", {
			name: "Use Bifrost with AI",
		});
		await expect(dialog).toBeVisible();
		await expect(page.getByRole("menu")).toHaveCount(0);
		await expect(
			dialog.getByRole("button", { name: "Download Agent Plugin" }),
		).toBeVisible();
		await dialog
			.getByRole("button", { name: "Close", exact: true })
			.click();
		await expect(dialog).toBeHidden();
		await expect(
			page.getByRole("button", { name: "Account menu" }),
		).toBeFocused();
		await page.getByRole("button", { name: "Account menu" }).click();
		await page.getByRole("menuitem", { name: /Open Editor/ }).click();
		const editor = page.getByRole("dialog", { name: "Code editor" });
		await expect(editor).toBeVisible();
		await editor
			.getByRole("button", { name: "Close editor", exact: true })
			.click();
		await expect(editor).toBeHidden();
		await expect(
			page.getByRole("button", { name: "Account menu" }),
		).toBeFocused();
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= innerWidth,
			),
		).toBe(true);
	}
});
