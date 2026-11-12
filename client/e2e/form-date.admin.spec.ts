import { test, expect } from "./fixtures/api-fixture";

test("date and datetime fields use shared calendars and preserve their values", async ({
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
				content: `from bifrost import workflow\n\n@workflow(name="${name}")\nasync def ${name}(start_date: str, appointment: str):\n    return {"start_date": start_date, "appointment": appointment}\n`,
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
							name: "appointment",
							label: "Appointment",
							type: "datetime",
							required: true,
							default_value: "2026-11-10T09:00",
						},
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
		await expect(previewDate).toHaveText("Nov 10, 2026");
		await expect(previewDate).toBeDisabled();
		const previewDatetime = page
			.getByRole("tabpanel", { name: "Preview" })
			.getByLabel("Appointment", { exact: true });
		await expect(previewDatetime).toHaveText("Nov 10, 2026 · 9:00 AM");
		await expect(previewDatetime).toBeDisabled();

		await page.goto(`/execute/${formId}`);
		const date = page.getByLabel("Start Date");
		await expect(date).toHaveText("Nov 10, 2026");
		await date.click();
		await page.getByRole("button", { name: /November 20.*2026/i }).click();
		await expect(date).toHaveText("Nov 20, 2026");
		const appointment = page.getByLabel("Appointment", { exact: true });
		await appointment.click();
		const dateTimeDialog = page.getByRole("dialog").filter({
			has: page.getByLabel("Time", { exact: true }),
		});
		await dateTimeDialog
			.getByRole("button", { name: /November 20.*2026/i })
			.click();
		await dateTimeDialog.getByLabel("Time", { exact: true }).fill("14:30");
		await page.keyboard.press("Escape");
		await expect(appointment).toHaveText("Nov 20, 2026 · 2:30 PM");
		const submitted = page.waitForResponse(
			(r) =>
				r.url().includes(`/api/forms/${formId}/submissions`) &&
				r.request().method() === "POST",
		);
		await page.getByRole("button", { name: "Submit", exact: true }).click();
		const response = await submitted;
		expect(response.ok(), await response.text()).toBe(true);
		const submission = (await response.json()) as { execution_id: string };
		const execution = await api.get(
			`/api/executions/${submission.execution_id}`,
		);
		expect(execution.ok(), await execution.text()).toBe(true);
		expect(await execution.json()).toMatchObject({
			input_data: {
				start_date: "2026-11-20",
				appointment: "2026-11-20T14:30",
			},
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
