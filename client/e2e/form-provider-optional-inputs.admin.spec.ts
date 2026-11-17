import { test, expect } from "./fixtures/api-fixture";

// One live journey proves signature discovery -> sanitized runtime -> provider execution.
test("form choices load with an empty optional middle name", async ({
	page,
	api,
}) => {
	const suffix = crypto.randomUUID().replaceAll("-", "");
	const path = `optional_provider_${suffix}.py`;
	const providerName = `optional_names_${suffix}`;
	let formId: string | undefined;
	try {
		const written = await api.put("/api/files/editor/content", {
			data: {
				path,
				encoding: "utf-8",
				content: `from bifrost import data_provider

@data_provider(name="${providerName}")
async def ${providerName}(first_name: str, middle_name: str = ""):
    return [{"value": first_name + middle_name, "label": first_name + middle_name}]
`,
			},
		});
		expect(written.ok(), await written.text()).toBe(true);
		const registered = await api.post("/api/workflows/register", {
			data: {
				path,
				function_name: providerName,
			},
		});
		expect(registered.ok(), await registered.text()).toBe(true);
		const workflows = await api.get("/api/workflows");
		expect(workflows.ok()).toBe(true);
		const provider = (await workflows.json()).find(
			(item: { name: string }) => item.name === providerName,
		);
		expect(provider).toBeTruthy();
		const created = await api.post("/api/forms", {
			data: {
				name: `Optional provider ${suffix}`,
				access_level: "authenticated",
				form_schema: {
					fields: [
						{
							name: "first",
							label: "First Name",
							type: "text",
							required: true,
						},
						{ name: "middle", label: "Middle Name", type: "text" },
						{
							name: "username",
							label: "Username",
							type: "select",
							data_provider_id: provider.id,
							data_provider_inputs: {
								first_name: {
									mode: "fieldRef",
									field_name: "first",
								},
								middle_name: {
									mode: "fieldRef",
									field_name: "middle",
								},
							},
						},
					],
				},
			},
		});
		expect(created.ok(), await created.text()).toBe(true);
		formId = (await created.json()).id;
		const runtime = await api.get(`/api/forms/${formId}/runtime`);
		expect(runtime.ok(), await runtime.text()).toBe(true);
		expect(
			(await runtime.json()).form_schema.fields.find(
				(field: { name: string }) => field.name === "username",
			).optional_provider_inputs,
		).toEqual(["middle_name"]);
		await page.goto(`/execute/${formId}`);
		await page.getByLabel("First Name", { exact: false }).fill("Alex");
		await page.getByLabel("Middle Name").focus();
		await expect(page.getByLabel("Middle Name")).toHaveValue("");
		await page
			.getByRole("combobox", { name: "Username", exact: true })
			.click();
		await expect(
			page.getByRole("option", { name: "Alex", exact: true }),
		).toBeVisible();
		await page.getByRole("option", { name: "Alex", exact: true }).click();
		await expect(
			page.getByRole("combobox", { name: "Username", exact: true }),
		).toContainText("Alex");
	} finally {
		if (formId) {
			const deleted = await api.delete(`/api/forms/${formId}`);
			expect([200, 204, 404]).toContain(deleted.status());
		}
		const deleted = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(path)}`,
		);
		expect([200, 204, 404]).toContain(deleted.status());
	}
});
