import { test, expect } from "./fixtures/api-fixture";

test("date fields use a native picker and submit a calendar date", async ({
	page,
	api,
}) => {
	const suffix = `${Date.now()}_${Math.floor(Math.random() * 10000)}`;
	const name = `date_form_${suffix}`;
	const path = `${name}.py`;
	let formId: string | undefined;
	try {
		const write = await api.put("/api/files/editor/content", {
			data: {
				path,
				encoding: "utf-8",
				content: `from bifrost import workflow\n\n@workflow(name="${name}")\nasync def ${name}(start_date: str):\n    return {"start_date": start_date}\n`,
			},
		});
		expect(write.ok(), await write.text()).toBe(true);
		const registration = await api.post("/api/workflows/register", {
			data: { path, function_name: name },
		});
		expect(registration.ok(), await registration.text()).toBe(true);
		const workflows = await api.get("/api/workflows");
		expect(workflows.ok(), await workflows.text()).toBe(true);
		const workflow = (
			(await workflows.json()) as Array<{ id: string; name: string }>
		).find((w) => w.name === name);
		expect(workflow).toBeTruthy();
		const created = await api.post("/api/forms", {
			data: {
				name,
				workflow_id: workflow!.id,
				access_level: "authenticated",
				form_schema: {
					fields: [
						{
							name: "start_date",
							label: "Start Date",
							type: "date",
							required: true,
							default_value: "2026-11-10",
						},
					],
				},
			},
		});
		expect(created.ok(), await created.text()).toBe(true);
		formId = ((await created.json()) as { id: string }).id;
		await page.goto(`/forms/${formId}/edit`);
		await page.getByRole("tab", { name: "Preview" }).click();
		const previewDate = page
			.getByRole("tabpanel", { name: "Preview" })
			.getByLabel("Start Date");
		await expect(previewDate).toHaveAttribute("type", "date");
		await expect(previewDate).toHaveValue("2026-11-10");
		await expect(previewDate).toBeDisabled();

		await page.goto(`/execute/${formId}`);
		const date = page.getByLabel("Start Date");
		await expect(date).toHaveAttribute("type", "date");
		await expect(date).toHaveValue("2026-11-10");
		await date.fill("2026-11-20");
		const submitted = page.waitForResponse(
			(r) =>
				r.url().includes(`/api/forms/${formId}/submissions`) &&
				r.request().method() === "POST",
		);
		await page.getByRole("button", { name: "Submit", exact: true }).click();
		const response = await submitted;
		expect(response.ok(), await response.text()).toBe(true);
		const submission = (await response.json()) as { execution_id: string };
		const execution = await api.get(`/api/executions/${submission.execution_id}`);
		expect(execution.ok(), await execution.text()).toBe(true);
		expect(await execution.json()).toMatchObject({
			input_data: { start_date: "2026-11-20" },
		});
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
