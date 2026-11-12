import { test, expect } from "./fixtures/api-fixture";
import type { Locator, Page } from "@playwright/test";
test.use({ video: "on" });

async function drag(page: Page, source: Locator, target: Locator) {
	await source.scrollIntoViewIfNeeded();
	const a = (await source.boundingBox())!;
	await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
	await page.mouse.down();
	await page.mouse.move(a.x + a.width / 2 + 5, a.y + a.height / 2 + 5, {
		steps: 5,
	});
	await target.scrollIntoViewIfNeeded();
	const b = (await target.boundingBox())!;
	await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
	await expect(page.getByRole("status", { name: /^Drop at / })).toHaveCount(
		1,
	);
	if ((await source.getAttribute("aria-label"))?.startsWith("Drag row")) {
		await page.screenshot({
			path: "playwright-results/form-drop-insertion.png",
		});
	}
	await page.mouse.up();
}
test("rows and palette fields share one canvas, persist and preview responsively @smoke", async ({
	page,
	api,
}) => {
	const unique = Date.now().toString();
	const fn = `form_canvas_${unique}`,
		path = `${fn}.py`;
	const write = await api.put("/api/files/editor/content", {
		data: {
			path,
			content: `from bifrost import workflow\n@workflow(name="${fn}")\nasync def ${fn}(first_name: str = "", email: str = ""):\n    return {"first_name": first_name, "email": email}\n`,
			encoding: "utf-8",
		},
	});
	expect(write.ok(), await write.text()).toBe(true);
	const register = await api.post("/api/workflows/register", {
		data: { path, function_name: fn },
	});
	expect(register.ok(), await register.text()).toBe(true);
	const workflows = await api.get("/api/workflows");
	const wf = (
		(await workflows.json()) as Array<{ id: string; name: string }>
	).find((w) => w.name === fn)!;
	const created = await api.post("/api/forms", {
		data: {
			name: `Canvas ${unique}`,
			workflow_id: wf.id,
			form_schema: { fields: [] },
			access_level: "authenticated",
		},
	});
	expect(created.ok(), await created.text()).toBe(true);
	const form = (await created.json()) as { id: string };
	try {
		await page.goto(`/forms/${form.id}/edit`);
		await drag(
			page,
			page.getByRole("button", { name: "Row", exact: true }),
			page.getByRole("region", { name: "Form Canvas", exact: true }),
		);
		await page
			.getByRole("combobox", { name: "Columns for row 1 in section 1" })
			.selectOption("6,6");
		for (const [name, label, type, column] of [
			["first_name", "First Name", "Text Input", 1],
			["email", "Email", "Email", 2],
		] as const) {
			await drag(
				page,
				page.getByRole("button", { name: type, exact: true }),
				page.getByRole("group", {
					name: `Column ${column} of row 1 in section 1`,
					exact: true,
				}),
			);
			const dialog = page.getByRole("dialog", {
				name: "Add Field",
				exact: true,
			});
			await expect(
				dialog.getByRole("combobox", { name: "Field Type" }),
			).toHaveText(type === "Text Input" ? "Text" : "Email");
			await dialog.getByLabel(/Field Name/).fill(name);
			await dialog.getByLabel(/^Label/).fill(label);
			await dialog
				.getByRole("button", { name: "Add Field", exact: true })
				.click();
			await expect(
				page
					.getByRole("group", {
						name: `Column ${column} of row 1 in section 1`,
						exact: true,
					})
					.getByRole("button", { name: `Edit ${label}` }),
			).toBeVisible();
		}
		await expect(page.getByText("Customize Layout")).toHaveCount(0);
		await drag(
			page,
			page.getByRole("button", { name: "Row", exact: true }),
			page.getByText("Drop a Field, Row, or Section Here", {
				exact: true,
			}),
		);
		await drag(
			page,
			page.getByRole("button", { name: "Drag field email" }),
			page.getByRole("group", {
				name: "Column 1 of row 2 in section 1",
				exact: true,
			}),
		);
		await expect(
			page
				.getByRole("group", {
					name: "Column 1 of row 2 in section 1",
					exact: true,
				})
				.getByRole("button", { name: "Edit Email" }),
		).toBeVisible();
		await drag(
			page,
			page.getByRole("button", { name: "Drag field email" }),
			page.getByRole("group", {
				name: "Column 2 of row 1 in section 1",
				exact: true,
			}),
		);
		await expect(
			page
				.getByRole("group", {
					name: "Column 2 of row 1 in section 1",
					exact: true,
				})
				.getByRole("button", { name: "Edit Email" }),
		).toBeVisible();
		await drag(
			page,
			page.getByRole("button", { name: "Section", exact: true }),
			page.getByText("Drop a Field, Row, or Section Here", {
				exact: true,
			}),
		);
		await page
			.getByRole("textbox", { name: "Section 2 Title", exact: true })
			.fill("Contact Details");
		await drag(
			page,
			page.getByRole("button", {
				name: "Drag row 1 in section 1",
				exact: true,
			}),
			page.getByRole("button", {
				name: "Drop a Row or Field Here",
				exact: true,
			}),
		);
		await expect(
			page
				.getByRole("group", {
					name: "Column 2 of row 1 in section 2",
					exact: true,
				})
				.getByRole("button", { name: "Edit Email" }),
		).toBeVisible();
		await drag(
			page,
			page.getByRole("button", { name: "Drag section 2", exact: true }),
			page.getByRole("region", { name: "Section 1", exact: true }),
		);
		await expect(
			page.getByRole("textbox", { name: "Section 1 Title", exact: true }),
		).toHaveValue("Contact Details");
		// A final named section must never capture drops onto the form footer.
		const footer = page.getByText("Drop a Field, Row, or Section Here", {
			exact: true,
		});
		await drag(
			page,
			page.getByRole("button", { name: "Section", exact: true }),
			footer,
		);
		await page
			.getByRole("textbox", { name: "Section 3 Title", exact: true })
			.fill("Other Section");
		await drag(
			page,
			page.getByRole("button", { name: "Row", exact: true }),
			footer,
		);
		await expect(
			page
				.getByRole("region", { name: "Section 3", exact: true })
				.getByRole("combobox"),
		).toHaveCount(0);
		await expect(
			page.getByRole("combobox", {
				name: "Columns for row 1 in section 4",
			}),
		).toBeVisible();
		await drag(
			page,
			page.getByRole("button", { name: "Drag field email" }),
			footer,
		);
		await expect(
			page
				.getByRole("region", { name: "Field email", exact: true })
				.getByRole("combobox"),
		).toHaveCount(0);
		await expect(
			page
				.getByRole("region", { name: "Field email", exact: true })
				.getByRole("button", { name: "Edit Email" }),
		).toBeVisible();
		await drag(
			page,
			page.getByRole("button", { name: "Drag field email" }),
			page.getByRole("group", {
				name: "Column 2 of row 1 in section 1",
				exact: true,
			}),
		);
		await drag(
			page,
			page.getByRole("button", { name: "Text Input", exact: true }),
			footer,
		);
		const standaloneDialog = page.getByRole("dialog", {
			name: "Add Field",
			exact: true,
		});
		await standaloneDialog.getByLabel(/Field Name/).fill("notes");
		await standaloneDialog.getByLabel(/^Label/).fill("Notes");
		await standaloneDialog
			.getByRole("button", { name: "Add Field", exact: true })
			.click();
		await expect(
			page
				.getByRole("region", { name: "Field notes", exact: true })
				.getByRole("combobox"),
		).toHaveCount(0);
		const save = page.waitForResponse(
			(r) =>
				r.url().endsWith(`/api/forms/${form.id}`) &&
				r.request().method() === "PATCH",
		);
		await page.getByRole("button", { name: "Save", exact: true }).click();
		const saved = await save;
		expect(saved.ok(), await saved.text()).toBe(true);
		const payload = await saved.json();
		expect(
			payload.form_schema.fields.find(
				(f: { name: string }) => f.name === "email",
			).type,
		).toBe("email");
		await page.waitForURL("**/forms");
		await page.goto(`/forms/${form.id}/edit`);
		await expect(
			page
				.getByRole("group", {
					name: "Column 2 of row 1 in section 1",
					exact: true,
				})
				.getByRole("button", { name: "Edit Email" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", {
				name: "Add field to column 1 of row 1 in section 2",
			}),
		).toBeVisible();
		await expect(
			page
				.getByRole("region", { name: "Field notes", exact: true })
				.getByRole("button", { name: "Edit Notes" }),
		).toBeVisible();
		await expect(
			page.getByRole("textbox", { name: "Section 4 Title", exact: true }),
		).toHaveCount(0);
		await page.screenshot({
			path: "playwright-results/form-unified-canvas.png",
		});
		await page.getByRole("tab", { name: "Preview", exact: true }).click();
		const first = page.getByLabel("First Name", { exact: true }),
			email = page.getByLabel("Email", { exact: true });
		await expect(first).toBeVisible();
		await expect(email).toBeVisible();
		expect((await first.boundingBox())!.y).toBeCloseTo(
			(await email.boundingBox())!.y,
			0,
		);
		await page.setViewportSize({ width: 390, height: 844 });
		await expect
			.poll(
				async () =>
					((await email.boundingBox())?.y ?? 0) >
					((await first.boundingBox())?.y ?? 0),
			)
			.toBe(true);
	} finally {
		await api.delete(`/api/forms/${form.id}`);
		await api.delete(`/api/workflows/${wf.id}`);
		await api.delete("/api/files", { params: { path } });
	}
});
