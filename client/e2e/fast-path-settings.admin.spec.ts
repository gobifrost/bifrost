import { randomUUID } from "node:crypto";
import { expect, test } from "./fixtures/api-fixture";
import type { components } from "../src/lib/v1";

test("an admin reserves Fast Path capacity and explicitly keeps a long App workflow Normal", async ({
	page,
	api,
}, testInfo) => {
	const name = `lane_settings_${randomUUID().replaceAll("-", "")}`;
	const path = `${name}.py`;
	const before = await api.get("/api/admin/kubernetes/execution");
	expect(before.ok()).toBeTruthy();
	const original = (await before.json())
		.policy as components["schemas"]["KubernetesPolicyValues"];
	let workflowId: string | undefined;
	try {
		const write = await api.put("/api/files/editor/content", {
			data: {
				path,
				encoding: "utf-8",
				content: `from bifrost import workflow\n\n@workflow(name="${name}")\nasync def ${name}() -> dict:\n    return {"ok": True}\n`,
			},
		});
		expect(write.ok(), await write.text()).toBeTruthy();
		const registered = await api.post("/api/workflows/register", {
			data: { path, function_name: name },
		});
		expect(registered.ok(), await registered.text()).toBeTruthy();
		workflowId = (await registered.json()).id;

		await page.goto("/settings/ai");
		await page
			.getByRole("button", { name: "Workers", exact: true })
			.click();
		await page
			.getByRole("button", { name: "Scaling & Isolation", exact: true })
			.click();
		await page.getByLabel("Reserved Fast Path Workers").fill("1");
		await page
			.getByLabel("Max Workers", { exact: true })
			.fill(
				String(
					Math.max(
						original.max_workers,
						original.warm_workers + 1,
						2,
					),
				),
			);
		const [saved] = await Promise.all([
			page.waitForResponse(
				(response) =>
					response.url().endsWith("/api/admin/kubernetes/policy") &&
					response.request().method() === "PUT",
			),
			page
				.getByRole("button", { name: "Save Scaling", exact: true })
				.click(),
		]);
		expect(saved.ok(), await saved.text()).toBeTruthy();
		expect((await saved.json()).policy.reserved_fast_path_workers).toBe(1);
		const appPriority = page.getByRole("switch", {
			name: "Prioritize App Workflows",
		});
		if (!(await appPriority.isChecked())) await appPriority.click();
		await expect(appPriority).toBeChecked();
		await page.reload();
		await expect(page.getByLabel("Reserved Fast Path Workers")).toHaveValue(
			"1",
		);
		await expect(
			page.getByRole("switch", { name: "Prioritize App Workflows" }),
		).toBeChecked();
		await expect(
			page
				.getByRole("status")
				.filter({ hasText: "reserved workers ready at last refresh" }),
		).toContainText("0 of 1");
		await page.screenshot({
			path: testInfo.outputPath("fast-path-settings-desktop.png"),
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page.emulateMedia({
			colorScheme: "dark",
			reducedMotion: "reduce",
		});
		await expect(
			page.getByLabel("Reserved Fast Path Workers"),
		).toBeVisible();
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= window.innerWidth,
			),
		).toBe(true);
		await page.screenshot({
			path: testInfo.outputPath("fast-path-settings-mobile.png"),
			fullPage: true,
		});
		await page.setViewportSize({ width: 1280, height: 900 });

		await page.goto("/workflows");
		await page
			.getByPlaceholder("Search by name, description, or category...")
			.fill(name);
		await page
			.getByRole("button", { name: `${name} actions`, exact: true })
			.click();
		await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
		const dialog = page.getByRole("dialog", {
			name: "Edit Workflow Settings",
		});
		await dialog
			.getByRole("tab", { name: "Execution", exact: true })
			.click();
		await dialog.getByRole("combobox", { name: "Priority" }).click();
		await page.getByRole("option", { name: "Normal", exact: true }).click();
		await expect(
			dialog.getByRole("combobox", { name: "Priority" }),
		).toHaveText("Normal");
		await page.screenshot({
			path: testInfo.outputPath("workflow-priority.png"),
		});
		await dialog
			.getByRole("button", { name: "Save Changes", exact: true })
			.click();
		await expect(dialog).toBeHidden();
		const workflows = await api.get("/api/workflows");
		expect(workflows.ok()).toBeTruthy();
		expect(
			(await workflows.json()).find(
				(workflow: components["schemas"]["WorkflowMetadata"]) =>
					workflow.id === workflowId,
			),
		).toMatchObject({ execution_lane: "normal" });
	} finally {
		if (workflowId) {
			const deleted = await api.delete(`/api/workflows/${workflowId}`);
			expect([200, 204, 404]).toContain(deleted.status());
		}
		const removed = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(path)}`,
		);
		expect([200, 204, 404]).toContain(removed.status());
		const restored = await api.put("/api/admin/kubernetes/policy", {
			data: {
				reserved_fast_path_workers: original.reserved_fast_path_workers,
				auto_priority_app_workflows:
					original.auto_priority_app_workflows,
				max_workers: original.max_workers,
			},
		});
		expect(restored.ok(), await restored.text()).toBeTruthy();
	}
});
