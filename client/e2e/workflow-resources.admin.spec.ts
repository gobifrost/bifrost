import { randomUUID } from "node:crypto";
import { expect, test } from "./fixtures/api-fixture";

test("an administrator finds a workflow in resource runs and its ranking", async ({
	page,
	api,
}) => {
	const suffix = randomUUID().replace(/-/g, "_");
	const name = `resource_usage_${suffix}`;
	const path = `${name}.py`;
	const source = `from bifrost import workflow\n\n@workflow(name="${name}")\nasync def ${name}() -> dict:\n    total = sum(range(10000))\n    return {"total": total}\n`;
	let workflowId: string | undefined;
	try {
		const write = await api.put("/api/files/editor/content", {
			data: { path, content: source, encoding: "utf-8" },
		});
		expect(write.ok(), await write.text()).toBe(true);
		const register = await api.post("/api/workflows/register", {
			data: { path, function_name: name },
		});
		expect(register.ok(), await register.text()).toBe(true);
		workflowId = ((await register.json()) as { id: string }).id;
		const execute = await api.post("/api/workflows/execute", {
			data: {
				workflow_id: workflowId,
				input_data: {},
				form_id: null,
				transient: false,
				sync: true,
			},
		});
		expect(execute.ok(), await execute.text()).toBe(true);
		const executionId = ((await execute.json()) as { execution_id: string })
			.execution_id;

		await page.goto("/reports/usage");
		await page.getByRole("radio", { name: "Workflow Resources" }).click();
		await expect(page).toHaveURL(/tab=workflow/);
		await expect(
			page.getByRole("heading", { name: "Usage" }),
		).toBeVisible();
		await page
			.getByRole("textbox", { name: "Search workflows" })
			.fill(name);
		await expect(page).toHaveURL(/workflow_search=/);
		await page.setViewportSize({ width: 1024, height: 768 });
		const dateButton = await page.locator("#date").boundingBox();
		expect(dateButton).not.toBeNull();
		expect(dateButton!.x + dateButton!.width).toBeLessThanOrEqual(1024);
		expect(
			await page
				.locator("#date")
				.evaluate(
					(element) => element.scrollWidth <= element.clientWidth,
				),
		).toBe(true);
		await expect(
			page.getByRole("columnheader", { name: "Peak CPU %" }),
		).toBeVisible();
		const runRow = page.getByRole("row").filter({
			has: page.getByText(name, { exact: true }),
		});
		await expect(runRow.getByRole("link", { name })).toHaveAttribute(
			"href",
			`/history/${executionId}`,
		);
		await expect(runRow.getByText(/^\d+ MiB$/)).toBeVisible();
		await page.getByRole("tab", { name: "By Workflow" }).click();
		await expect(
			page.getByRole("columnheader", { name: "Peak CPU %" }),
		).toBeVisible();
		await expect(page.getByRole("button", { name })).toBeVisible();
		await page.getByRole("button", { name }).click();
		await expect(page.getByRole("tab", { name: "Runs" })).toHaveAttribute(
			"data-state",
			"active",
		);
		await runRow.click();
		await expect(page).toHaveURL(new RegExp(`/history/${executionId}$`));
		await page.getByRole("button", { name: "Back to usage" }).click();
		await expect(page).toHaveURL(/tab=workflow/);
		await expect(
			page.getByRole("textbox", { name: "Search workflows" }),
		).toHaveValue(name);
		await expect(page.getByRole("tab", { name: "Runs" })).toHaveAttribute(
			"data-state",
			"active",
		);
	} finally {
		if (workflowId) {
			const remove = await api.delete(`/api/workflows/${workflowId}`);
			expect([200, 204, 404]).toContain(remove.status());
		}
		const removeFile = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(path)}`,
		);
		expect([200, 204, 404]).toContain(removeFile.status());
	}
});
