import { test, expect } from "./fixtures/api-fixture";

test("mobile navigation traps focus, closes with Escape, and restores its trigger @smoke", async ({
	page,
}) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/workflows");
	const trigger = page.getByRole("button", { name: "Open navigation" });
	await trigger.click();
	const drawer = page.getByRole("dialog", { name: "Navigation" });
	await expect(drawer).toBeVisible();
	await page.keyboard.press("Shift+Tab");
	expect(
		await drawer.evaluate((node) => node.contains(document.activeElement)),
	).toBe(true);
	await page.keyboard.press("Escape");
	await expect(drawer).toBeHidden();
	await expect(trigger).toBeFocused();
	await trigger.click();
	await drawer
		.getByRole("link", { name: "Organizations", exact: true })
		.click();
	await expect(page).toHaveURL(/\/organizations$/);
	await expect(drawer).toBeHidden();
	await expect(
		page.getByRole("heading", { name: "Organizations", exact: true }),
	).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
	test(`custom branding keeps actual action text readable in ${theme} mode @smoke`, async ({
		page,
	}) => {
		await page.addInitScript(
			(t) => localStorage.setItem("theme", t),
			theme,
		);
		await page.route("**/api/branding", (route) =>
			route.fulfill({ json: { primary_color: "#fff200" } }),
		);
		await page.goto("/organizations");
		const action = page.getByRole("button", { name: "New Organization" });
		await expect(action).toBeVisible();
		const colors = await action.evaluate((node) => {
			const style = getComputedStyle(node);
			return {
				foreground: style.color,
				background: style.backgroundColor,
			};
		});
		const luminance = (css: string) => {
			const rgb = css
				.match(/[\d.]+/g)!
				.slice(0, 3)
				.map(Number)
				.map((n) => {
					const channel = n / 255;
					return channel <= 0.04045
						? channel / 12.92
						: ((channel + 0.055) / 1.055) ** 2.4;
				});
			return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
		};
		const foreground = luminance(colors.foreground),
			background = luminance(colors.background);
		expect(
			(Math.max(foreground, background) + 0.05) /
				(Math.min(foreground, background) + 0.05),
		).toBeGreaterThanOrEqual(4.5);
		const gradient = await page.evaluate(() =>
			getComputedStyle(document.documentElement).getPropertyValue(
				"--bf-activity-gradient",
			),
		);
		expect(gradient).toContain("linear-gradient");
		expect(gradient).not.toContain("#ff5a5a");
		await action.click();
		await expect(page.getByRole("dialog")).toBeVisible();
	});
}

test("a mobile designer can add a field by tapping the palette and inspect its preview @smoke", async ({
	page,
}) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/forms/new");
	await page
		.getByRole("dialog", { name: "Form Information" })
		.getByRole("button", { name: "Close", exact: true })
		.click();
	await page
		.getByRole("button", { name: "Choose a Component", exact: true })
		.click();
	await page.getByRole("button", { name: "Text Input", exact: true }).click();
	const field = page.getByRole("dialog", { name: "Add Field", exact: true });
	await field.getByLabel(/Field Name/).fill("review_name");
	await field.getByLabel(/^Label/).fill("Review name");
	await field.getByRole("button", { name: "Add Field", exact: true }).click();
	await expect(field).toBeHidden();
	await page.getByRole("tab", { name: "Preview", exact: true }).click();
	await expect(page.getByLabel("Review name", { exact: true })).toBeVisible();
});
