import { test, expect } from "./fixtures/api-fixture";

test("mobile field context closes independently and preserves editing", async ({
	page,
	api,
}) => {
	const key = `field_context_${Date.now()}`;
	const path = `${key}.py`;
	let formId: string | undefined;
	try {
		const write = await api.put("/api/files/editor/content", {
			data: {
				path,
				content: `from bifrost import workflow\n@workflow(name="${key}")\nasync def ${key}():\n    return {}\n`,
				encoding: "utf-8",
			},
		});
		expect(write.ok(), await write.text()).toBe(true);
		const register = await api.post("/api/workflows/register", {
			data: { path, function_name: key },
		});
		expect(register.ok(), await register.text()).toBe(true);
		const workflows = await api.get("/api/workflows");
		const workflow = (await workflows.json()).find(
			(row: { name: string }) => row.name === key,
		);
		const create = await api.post("/api/forms", {
			data: {
				name: key,
				workflow_id: workflow.id,
				form_schema: {
					fields: [
						{ name: "summary", label: "Summary", type: "text" },
					],
				},
				access_level: "authenticated",
			},
		});
		expect(create.ok(), await create.text()).toBe(true);
		formId = (await create.json()).id;
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(`/forms/${formId}/edit`);
		await page
			.getByRole("button", { name: "Edit Summary", exact: true })
			.click();
		const editor = page.getByRole("dialog", {
			name: "Edit Field",
			exact: true,
		});
		await editor.getByLabel(/^Label/).fill("Retained Summary");
		const trigger = editor.getByRole("button", {
			name: "Context",
			exact: true,
		});
		await trigger.click();
		const sheet = page.getByRole("dialog", {
			name: "Context & Helpers",
			exact: true,
		});
		await expect(
			sheet.getByText("Available Context", { exact: true }),
		).toBeVisible();
		await page.screenshot({
			path: "playwright-results/field-context-mobile.png",
			animations: "disabled",
		});
		await page.keyboard.press("Escape");
		await expect(sheet).toBeHidden();
		await expect(trigger).toBeFocused();
		await expect(editor.getByLabel(/^Label/)).toHaveValue(
			"Retained Summary",
		);
		await page.emulateMedia({
			colorScheme: "dark",
			reducedMotion: "reduce",
		});
		await page.setViewportSize({ width: 844, height: 390 });
		await trigger.click();
		await expect(sheet).toBeVisible();
		await sheet
			.getByText("Available Context", { exact: true })
			.scrollIntoViewIfNeeded();
		await page.screenshot({
			path: "playwright-results/field-context-landscape.png",
			animations: "disabled",
		});
		await sheet.getByRole("button", { name: "Close", exact: true }).click();
		await expect(sheet).toBeHidden();
		await expect(editor).toBeVisible();
		await expect(editor.getByLabel(/^Label/)).toHaveValue(
			"Retained Summary",
		);
	} finally {
		if (formId)
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/forms/${formId}`)).status(),
			);
		expect([200, 204, 404]).toContain(
			(
				await api.delete(
					`/api/files/editor?path=${encodeURIComponent(path)}`,
				)
			).status(),
		);
	}
});
