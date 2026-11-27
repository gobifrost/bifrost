import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

test("integration workflow shortcuts persist across reload and launch the linked workflow", async ({
	page,
	api,
}) => {
	const suffix = randomUUID().replaceAll("-", "");
	const names = [`sync_${suffix}`, `verify_${suffix}`];
	const paths: string[] = [];
	let integrationId: string | undefined;
	try {
		for (const name of names) {
			const path = `${name}.py`;
			paths.push(path);
			const write = await api.put("/api/files/editor/content", {
				data: {
					path,
					encoding: "utf-8",
					content: `from bifrost import workflow\n\n@workflow(name="${name}")\nasync def ${name}() -> dict:\n    return {"ok": True}\n`,
				},
			});
			expect(write.ok(), await write.text()).toBe(true);
			const registered = await api.post("/api/workflows/register", {
				data: { path, function_name: name },
			});
			expect(registered.ok(), await registered.text()).toBe(true);
		}
		const created = await api.post("/api/integrations", {
			data: { name: `Linked Directory ${suffix}` },
		});
		expect(created.ok(), await created.text()).toBe(true);
		integrationId = (await created.json()).id;
		await page.goto(`/integrations/${integrationId}`);
		const actions = page.getByRole("group", {
			name: "Integration workflows",
		});
		await actions
			.getByRole("button", { name: "Workflows", exact: true })
			.click();
		await page
			.getByRole("menuitem", { name: "Manage Linked Workflows" })
			.click();
		await page
			.getByRole("combobox", { name: "Workflow", exact: true })
			.click();
		await page.getByRole("option", { name: names[0], exact: true }).click();
		await page
			.getByRole("textbox", { name: "Button Label" })
			.fill("Sync Now");
		await page
			.getByRole("button", { name: "Link Workflow", exact: true })
			.click();
		await page.getByRole("checkbox", { name: "Pin as Top Button" }).check();
		await page
			.getByRole("combobox", { name: "Workflow", exact: true })
			.click();
		await page
			.getByRole("option", {
				name: names[1],
				exact: true,
			})
			.click();
		await page
			.getByRole("textbox", { name: "Button Label" })
			.fill("Verify Settings");
		await page
			.getByRole("button", { name: "Link Workflow", exact: true })
			.click();
		await page
			.getByRole("checkbox", { name: "Pin as Top Button" })
			.nth(1)
			.check();
		await page.setViewportSize({ width: 1920, height: 1000 });
		await page
			.getByRole("button", { name: "Save Changes", exact: true })
			.click();
		await expect(
			page.getByRole("dialog", { name: "Linked Workflows" }),
		).toBeHidden();
		await page.reload();
		await expect(
			actions.getByRole("button", {
				name: "Verify Settings",
				exact: true,
			}),
		).toBeVisible();
		await page.setViewportSize({ width: 390, height: 844 });
		await expect(page.getByRole("banner")).toHaveCSS("height", "64px");
		await page.getByRole("button", { name: "Account menu" }).click();
		await page
			.getByRole("menuitem", { name: "Appearance", exact: true })
			.click();
		await expect(
			page.getByRole("menuitemcheckbox", { name: "Presentation Mode" }),
		).toBeVisible();
		await page.keyboard.press("Escape");
		await page.keyboard.press("Escape");

		await expect(
			actions.getByRole("button", { name: "Sync Now", exact: true }),
		).toBeVisible();
		await expect(
			actions.getByRole("button", {
				name: "Verify Settings",
				exact: true,
			}),
		).toHaveCount(0);
		const first = await actions
			.getByRole("button", { name: "Sync Now", exact: true })
			.boundingBox();
		const menu = await actions
			.getByRole("button", { name: /Workflows/ })
			.boundingBox();
		expect(first?.y).toBe(menu?.y);
		await actions.getByRole("button", { name: /Workflows/ }).click();
		await expect(
			page.getByRole("menuitem", { name: /Sync Now/ }),
		).toBeVisible();
		await page.getByRole("menuitem", { name: /Verify Settings/ }).click();
		await expect(page.getByRole("dialog")).toContainText(names[1]);
		await page
			.getByRole("dialog")
			.getByRole("button", { name: "Close", exact: true })
			.click();
		await actions
			.getByRole("button", { name: "Sync Now", exact: true })
			.click();
		const accepted = page.waitForResponse(
			(r) =>
				r.url().endsWith("/api/workflows/execute") &&
				r.request().method() === "POST",
		);
		await page
			.getByRole("button", { name: "Run Workflow", exact: true })
			.click();
		const run = await (await accepted).json();
		await expect(
			page.getByRole("link", { name: "Open execution in new tab" }),
		).toHaveAttribute("href", `/history/${run.execution_id}`);
		await expect(page.getByRole("dialog")).toContainText(
			/Running|Completed/,
		);
	} finally {
		if (integrationId)
			expect(
				(await api.delete(`/api/integrations/${integrationId}`)).ok(),
			).toBe(true);
		for (const path of paths) {
			const removed = await api.delete(
				`/api/files/editor?path=${encodeURIComponent(path)}`,
			);
			expect([200, 204, 404]).toContain(removed.status());
		}
	}
});
