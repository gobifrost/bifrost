import { test, expect } from "./fixtures/api-fixture";

test("number fields accept decimal defaults and submit edited decimals", async ({
	page,
	api,
}) => {
	const name = `decimal_form_${crypto.randomUUID().replaceAll("-", "")}`;
	const path = `${name}.py`;
	let formId: string | undefined;
	try {
		const written = await api.put("/api/files/editor/content", {
			data: {
				path,
				encoding: "utf-8",
				content: `from bifrost import workflow\n\n@workflow\nasync def ${name}(annual_wage: float, adjustment: float):\n    return {"annual_wage": annual_wage, "adjustment": adjustment}\n`,
			},
		});
		expect(written.ok(), await written.text()).toBe(true);
		const registered = await api.post("/api/workflows/register", {
			data: { path, function_name: name },
		});
		expect(registered.ok(), await registered.text()).toBe(true);
		const workflow = (await registered.json()) as { id: string };
		const created = await api.post("/api/forms", {
			data: {
				name,
				workflow_id: workflow.id,
				access_level: "authenticated",
				form_schema: {
					fields: [
						{
							name: "annual_wage",
							label: "Annual Wage",
							type: "number",
							required: true,
							default_value: 12345.67,
						},
						{
							name: "adjustment",
							label: "Adjustment",
							type: "number",
							required: true,
							default_value: 2.5,
						},
					],
				},
			},
		});
		expect(created.ok(), await created.text()).toBe(true);
		formId = ((await created.json()) as { id: string }).id;
		await page.goto(`/execute/${formId}`);
		const wage = page.getByRole("spinbutton", { name: "Annual Wage" });
		const adjustment = page.getByRole("spinbutton", { name: "Adjustment" });
		await expect(wage).toHaveValue("12345.67");
		await expect(adjustment).toHaveValue("2.5");
		expect(
			await wage.evaluate(
				(input: HTMLInputElement) => input.validity.valid,
			),
		).toBe(true);
		expect(
			await adjustment.evaluate(
				(input: HTMLInputElement) => input.validity.valid,
			),
		).toBe(true);
		await wage.fill("12345.89");
		await adjustment.fill("2.75");
		const submitted = page.waitForResponse(
			(response) =>
				response.url().includes(`/api/forms/${formId}/submissions`) &&
				response.request().method() === "POST",
		);
		await page.getByRole("button", { name: "Submit", exact: true }).click();
		const response = await submitted;
		expect(response.ok(), await response.text()).toBe(true);
		const { execution_id } = (await response.json()) as {
			execution_id: string;
		};
		const execution = await api.get(`/api/executions/${execution_id}`);
		expect(execution.ok(), await execution.text()).toBe(true);
		expect(await execution.json()).toMatchObject({
			input_data: { annual_wage: 12345.89, adjustment: 2.75 },
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
