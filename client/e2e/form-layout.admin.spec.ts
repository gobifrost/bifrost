import { test, expect } from "./fixtures/api-fixture";
import type { Locator, Page } from "@playwright/test";

async function dragFromGrip(
	page: Page,
	source: Locator,
	target: Locator,
	before = false,
) {
	await source.scrollIntoViewIfNeeded();
	const sourceBox = await source.boundingBox();
	if (!sourceBox) throw new Error("Drag source is not visible");
	const startX = sourceBox.x + sourceBox.width / 2;
	const startY = sourceBox.y + sourceBox.height / 2;
	await page.mouse.move(startX, startY);
	await page.mouse.down();
	await page.mouse.move(startX + 5, startY + 5, { steps: 5 });
	await target.scrollIntoViewIfNeeded();
	const targetBox = await target.boundingBox();
	if (!targetBox) throw new Error("Drag target is not visible");
	await page.mouse.move(
		targetBox.x + targetBox.width / 2,
		before ? targetBox.y + 8 : targetBox.y + targetBox.height / 2,
		{ steps: 12 },
	);
	await page.mouse.up();
}

test("a designer can arrange form fields into a responsive row and preview it @smoke", async ({
	page,
}) => {
	await page.goto("/forms/new");
	await page
		.getByRole("dialog", { name: "Form Information" })
		.getByRole("button", { name: "Close", exact: true })
		.click();

	for (const [name, label] of [
		["first_name", "First Name"],
		["email", "Email"],
		["company", "Company"],
	] as const) {
		await page
			.getByRole("button", { name: "Text Input", exact: true })
			.click();
		const field = page.getByRole("dialog", {
			name: "Add Field",
			exact: true,
		});
		await field.getByLabel(/Field Name/).fill(name);
		await field.getByLabel(/^Label/).fill(label);
		await field
			.getByRole("button", { name: "Add Field", exact: true })
			.click();
	}

	await page.getByRole("button", { name: "Customize Layout" }).click();
	await page
		.getByPlaceholder("Optional section title")
		.fill("Contact details");
	await page.locator("#width-0-0-0").selectOption("6");
	await page.locator("#width-0-1-0").selectOption("6");
	await dragFromGrip(
		page,
		page.getByRole("button", { name: "Drag field email" }),
		page.getByRole("group", { name: "Field placement first_name" }),
	);
	await expect(page.getByLabel("Add field to row 1")).toBeDisabled();
	await page.getByRole("button", { name: "Add Section" }).click();
	await page.getByRole("button", { name: "Add Row" }).nth(1).click();
	await page.getByLabel("Section Title").nth(1).fill("Company details");
	await dragFromGrip(
		page,
		page.getByRole("button", { name: "Drag field company" }),
		page.getByLabel("Drop field into section 2 row 1"),
	);
	await dragFromGrip(
		page,
		page.getByRole("button", { name: "Drag row 2 in section 1" }),
		page.getByLabel("Drop row into section 2"),
	);
	await expect(
		page.getByRole("button", { name: "Drag row 2 in section 2" }),
	).toBeVisible();
	await dragFromGrip(
		page,
		page.getByRole("button", { name: "Drag section 2" }),
		page.getByRole("button", { name: "Drag section 1" }),
		true,
	);
	await expect(page.getByLabel("Section Title").first()).toHaveValue(
		"Company details",
	);
	await page.setViewportSize({ width: 1440, height: 1600 });
	await page
		.getByRole("region", { name: "Section 2", exact: true })
		.screenshot({
			path: "playwright-results/form-layout-drag-handles.png",
		});

	await page.getByRole("tab", { name: "Preview", exact: true }).click();
	const first = page.getByLabel("First Name", { exact: true });
	const email = page.getByLabel("Email", { exact: true });
	const companySection = page.locator("section").filter({
		has: page.getByRole("heading", { name: "Company details" }),
	});
	await expect(first).toBeVisible();
	await expect(email).toBeVisible();
	await expect(
		companySection.getByLabel("Company", { exact: true }),
	).toBeVisible();
	expect((await first.boundingBox())!.y).toBeCloseTo(
		(await email.boundingBox())!.y,
		0,
	);

	await page.setViewportSize({ width: 390, height: 844 });
	await expect(first).toBeVisible();
	await expect
		.poll(async () => {
			const [firstBox, emailBox] = await Promise.all([
				first.boundingBox(),
				email.boundingBox(),
			]);
			return (emailBox?.y ?? 0) > (firstBox?.y ?? 0);
		})
		.toBe(true);
});
