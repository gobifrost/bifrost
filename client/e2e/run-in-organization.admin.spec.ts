import { test, expect } from "./fixtures/api-fixture";

// One workflow launch and one form submission prove the two existing launch
// transports carry the selected scope all the way into worker execution.
test("runs a workflow and a form in the selected organization", async ({
	page,
	api,
}, testInfo) => {
	const suffix = crypto.randomUUID().replaceAll("-", "");
	const name = `org_context_${suffix}`;
	const path = `workflows/${name}.py`;
	let formId: string | undefined;
	const errors: unknown[] = [];
	try {
		const organizationsResponse = await api.get("/api/organizations");
		expect(organizationsResponse.ok()).toBe(true);
		const organizations = (await organizationsResponse.json()) as Array<{
			id: string;
			name: string;
			is_provider: boolean;
		}>;
		const target = organizations.find((org) => !org.is_provider)!;
		expect(target).toBeDefined();
		const source = `from bifrost import workflow, context\n\n@workflow(name="${name}")\nasync def ${name}(summary: str = "Scope check") -> dict:\n    return {"org_id": context.org_id, "summary": summary}\n`;
		const write = await api.put("/api/files/editor/content", {
			data: { path, content: source, encoding: "utf-8" },
		});
		expect(write.ok(), await write.text()).toBe(true);
		const register = await api.post("/api/workflows/register", {
			data: { path, function_name: name, organization_id: null },
		});
		expect(register.ok(), await register.text()).toBe(true);
		const workflow = (await register.json()) as { id: string };
		const created = await api.post("/api/forms", {
			data: {
				name: "Organization Scope Check",
				description:
					"Run a safe check against the selected organization.",
				workflow_id: workflow.id,
				organization_id: null,
				access_level: "authenticated",
				form_schema: {
					fields: [
						{
							name: "summary",
							label: "Summary",
							type: "text",
							required: true,
						},
					],
				},
			},
		});
		expect(created.ok(), await created.text()).toBe(true);
		formId = (await created.json()).id;

		const selectTarget = async () => {
			await page
				.getByRole("combobox", { name: "Run in Organization" })
				.click();
			await page
				.getByRole("option")
				.getByText(target.name, { exact: true })
				.click();
			await expect(page.getByRole("option")).toHaveCount(0);
		};
		const verifyExecution = async () => {
			await page.waitForURL(/\/history\/[^/]+$/);
			const executionId = page.url().split("/").at(-1)!;
			await expect
				.poll(async () => {
					const response = await api.get(
						`/api/executions/${executionId}`,
					);
					expect(response.ok()).toBe(true);
					return await response.json();
				})
				.toMatchObject({
					status: "Success",
					org_id: target.id,
					result: { org_id: target.id },
				});
		};
		await page.goto(`/workflows/${name}/execute`);
		await selectTarget();
		await page.screenshot({
			path: testInfo.outputPath("workflow-organization-desktop.png"),
		});
		await page
			.getByRole("button", { name: "Execute Workflow", exact: true })
			.click();
		await verifyExecution();

		await page.goto(`/execute/${formId}`);
		await page
			.getByRole("textbox", { name: /^Summary/ })
			.fill("Old organization data");
		await selectTarget();
		await expect(
			page.getByRole("textbox", { name: /^Summary/ }),
		).toHaveValue("");
		await page
			.getByRole("textbox", { name: /^Summary/ })
			.fill("Verify this organization's context");
		await page.screenshot({
			path: testInfo.outputPath("form-organization-desktop.png"),
		});
		await page.emulateMedia({ reducedMotion: "reduce" });
		await page.getByRole("button", { name: "Toggle theme" }).click();
		await expect(page.locator("html")).not.toHaveClass(/dark/);
		await page.screenshot({
			path: testInfo.outputPath("form-organization-light.png"),
		});
		await page.getByRole("button", { name: "Toggle theme" }).click();
		await expect(page.locator("html")).toHaveClass(/dark/);
		await page.setViewportSize({ width: 390, height: 844 });
		await page.screenshot({
			path: testInfo.outputPath("form-organization-mobile.png"),
		});
		await page.getByRole("button", { name: "Submit", exact: true }).click();
		await verifyExecution();
	} catch (error) {
		errors.push(error);
	} finally {
		const cleanup = await Promise.allSettled([
			...(formId ? [api.delete(`/api/forms/${formId}`)] : []),
			api.delete(`/api/files/editor?path=${encodeURIComponent(path)}`),
		]);
		for (const result of cleanup) {
			if (result.status === "rejected") errors.push(result.reason);
			else if (![200, 204, 404].includes(result.value.status()))
				errors.push(
					new Error(
						`Fixture cleanup failed: ${result.value.status()} ${await result.value.text()}`,
					),
				);
		}
	}
	if (errors.length)
		throw new AggregateError(
			errors,
			"Organization execution journey or cleanup failed",
		);
});
