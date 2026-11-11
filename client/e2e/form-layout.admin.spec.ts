import { test, expect } from "./fixtures/api-fixture";

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
	await page.getByLabel("Add field to row 1").selectOption("email");
	await expect(page.getByLabel("Add field to row 1")).toBeDisabled();

	await page.getByRole("tab", { name: "Preview", exact: true }).click();
	const first = page.getByLabel("First Name", { exact: true });
	const email = page.getByLabel("Email", { exact: true });
	await expect(first).toBeVisible();
	await expect(email).toBeVisible();
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
