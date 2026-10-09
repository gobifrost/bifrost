import { randomUUID } from "node:crypto";

import type { Page } from "@playwright/test";

import { test, expect } from "./fixtures/api-fixture";

async function createDeniedFileRead(page: Page, path: string) {
	return page.evaluate(async (deniedPath) => {
		const token = localStorage.getItem("bifrost_access_token");
		const response = await fetch("/api/files/read", {
			method: "POST",
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/json",
			},
			body: JSON.stringify({
				location: "audit-browser",
				scope: "global",
				path: deniedPath,
				mode: "cloud",
			}),
		});
		return response.status;
	}, path);
}

test("filters policy denials by file path and opens one in the detail drawer", async ({
	page,
}) => {
	const path = `browser-denial-${Date.now()}.txt`;
	await page.goto("/audit");
	await expect(
		page.getByRole("heading", { name: "Audit Log" }),
	).toBeVisible();
	expect(await createDeniedFileRead(page, path)).toBe(403);

	await page.getByRole("combobox", { name: "Action filter" }).click();
	await page.getByRole("option", { name: "Policy denials" }).click();
	await page.getByRole("combobox", { name: "Outcome filter" }).click();
	await page.getByRole("option", { name: "Failure" }).click();
	await page
		.getByRole("searchbox", { name: "Search audit events" })
		.fill(path);

	await expect(page.getByText(`audit-browser / ${path}`)).toBeVisible();
	const opener = page.getByRole("button", {
		name: "policy.deny",
		exact: true,
	});
	await expect(opener).toBeVisible();

	await opener.click();
	const drawer = page.getByRole("dialog", { name: "Audit Event" });
	await expect(drawer.getByText(path, { exact: true })).toBeVisible();
	await page.keyboard.press("Escape");
	await expect(drawer).toBeHidden();
	await expect(opener).toBeFocused();
});

/**
 * One real would-deny check: a provider-organization workflow runs unattended
 * (through its API key) as a custom identity whose reach is the provider
 * organization and Global, and switches scope into another organization. The
 * single worker run is the only way to record a check; it proves the recorded
 * check → drill-in → explain journey that no lower layer crosses.
 */
test.describe("Access Checks on a recorded check", () => {
	const suffix = randomUUID().replaceAll("-", "").slice(0, 12);
	const orgName = `Fabrikam ${suffix}`;
	const identityName = `Contoso Scope Probe ${suffix}`;
	const workflowName = `scope_probe_${suffix}`;
	const workflowPath = `${workflowName}.py`;
	let organizationId: string;
	let identityId: string;
	let workflowId: string;

	test.beforeAll(async ({ api }) => {
		const authorization = await api.get("/api/auth/authorization");
		expect(authorization.ok()).toBe(true);
		const providerId = (
			(await authorization.json()) as { provider_organization_id: string }
		).provider_organization_id;

		const org = await api.post("/api/organizations", {
			data: { name: orgName, domain: `fabrikam-${suffix}.gobifrost.dev` },
		});
		expect(org.ok(), `create organization: ${await org.text()}`).toBe(true);
		organizationId = ((await org.json()) as { id: string }).id;

		const write = await api.put("/api/files/editor/content", {
			data: {
				path: workflowPath,
				encoding: "utf-8",
				content: [
					"from bifrost import config, workflow",
					"",
					"",
					`@workflow(name="${workflowName}")`,
					`async def ${workflowName}() -> dict:`,
					"    try:",
					`        await config.get("scope_probe", scope="${organizationId}")`,
					"    except Exception as error:",
					'        return {"error": type(error).__name__}',
					'    return {"ok": True}',
					"",
				].join("\n"),
			},
		});
		expect(write.ok(), `write workflow: ${write.status()}`).toBe(true);
		const register = await api.post("/api/workflows/register", {
			data: { path: workflowPath, function_name: workflowName },
		});
		expect(
			register.ok(),
			`register workflow: ${await register.text()}`,
		).toBe(true);
		workflowId = ((await register.json()) as { id: string }).id;

		const identity = await api.post("/api/identities", {
			data: { name: identityName, organization_id: providerId },
		});
		expect(identity.ok(), `create identity: ${await identity.text()}`).toBe(
			true,
		);
		identityId = ((await identity.json()) as { id: string }).id;

		const configured = await api.patch(`/api/workflows/${workflowId}`, {
			data: {
				organization_id: providerId,
				run_identity_id: identityId,
				endpoint_enabled: true,
				clear_roles: false,
			},
		});
		expect(
			configured.ok(),
			`configure workflow: ${await configured.text()}`,
		).toBe(true);

		const key = await api.post("/api/workflow-keys", {
			data: { workflow_id: workflowId, description: "Access Checks e2e" },
		});
		expect(key.ok(), `create key: ${await key.text()}`).toBe(true);
		const rawKey = ((await key.json()) as { raw_key: string }).raw_key;
		const run = await api.post(`/api/endpoints/${workflowId}`, {
			headers: { "X-Bifrost-Key": rawKey },
		});
		expect(run.ok(), `run workflow: ${await run.text()}`).toBe(true);

		// The check is written as the run's scope switch request finishes.
		await expect
			.poll(async () => {
				const recorded = await api.get("/api/audit", {
					params: {
						action: "access.check",
						outcome: "failure",
						workflow_id: workflowId,
					},
				});
				expect(recorded.ok()).toBe(true);
				return ((await recorded.json()) as { entries: unknown[] })
					.entries.length;
			})
			.toBe(1);
	});

	test.afterAll(async ({ api }) => {
		if (workflowId) {
			const reset = await api.patch(`/api/workflows/${workflowId}`, {
				data: { run_identity_id: null },
			});
			expect([200, 404]).toContain(reset.status());
			const revoked = await api.delete(
				`/api/workflow-keys/${workflowId}`,
			);
			expect([204, 400, 404]).toContain(revoked.status());
		}
		const removed = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(workflowPath)}`,
		);
		expect([200, 204, 404]).toContain(removed.status());
		const removals = [
			identityId && `/api/identities/${identityId}`,
			organizationId && `/api/organizations/${organizationId}`,
		].filter((path): path is string => !!path);
		for (const path of removals) {
			const deleted = await api.delete(path);
			expect([200, 204, 404]).toContain(deleted.status());
		}
	});

	test("ACCESS-CHECKS-01 opens a recorded check and tests it again now", async ({
		page,
	}) => {
		await page.goto(
			`/audit/access-checks?group_by=workflow&key=${workflowId}`,
		);
		await expect(
			page.getByRole("heading", { level: 2, name: workflowName }),
		).toBeVisible();
		await expect(page.getByText("1 would-deny check")).toBeVisible();
		const row = page.locator("table tbody tr");
		await expect(row).toHaveCount(1);
		await expect(row).toContainText(identityName);
		await row.getByRole("button").first().click();

		const drawer = page.getByRole("dialog", { name: "Access Check" });
		const reachStep = (trace: string) =>
			drawer
				.getByRole("list", { name: trace })
				.getByRole("listitem")
				.filter({
					has: page.getByRole("heading", {
						name: "Target in Reach",
						exact: true,
					}),
				});
		await expect(reachStep("Then")).toContainText("Would Stop Here");

		await drawer.getByRole("button", { name: "Test Again Now" }).click();
		await expect(
			drawer.getByText("Nothing changed since then."),
		).toBeVisible();
		await expect(reachStep("Now")).toContainText("Would Stop Here");
	});
});
