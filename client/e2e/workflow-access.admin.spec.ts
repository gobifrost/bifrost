/**
 * Workflow access panel — admin happy path.
 *
 * A Platform Admin opens a Contoso workflow's settings on the Access tab: it
 * runs unattended as Contoso's Default Identity. They choose a custom Contoso
 * identity instead, which saves right away, and Recommended Access waits for
 * the workflow's first runs.
 */

import { randomUUID } from "node:crypto";

import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const SUFFIX = randomUUID().replaceAll("-", "").slice(0, 12);
const ORG_NAME = `Contoso ${SUFFIX}`;
const IDENTITY_NAME = `Contoso Ticket Sync ${SUFFIX}`;
const WORKFLOW_NAME = `access_panel_${SUFFIX}`;
const WORKFLOW_PATH = `${WORKFLOW_NAME}.py`;

async function readWorkflow(api: AuthedApi, id: string) {
	const response = await api.get("/api/workflows");
	expect(response.ok()).toBe(true);
	const workflows = (await response.json()) as Array<{
		id: string;
		run_identity_id: string | null;
	}>;
	const workflow = workflows.find((item) => item.id === id);
	expect(workflow, "Seeded workflow appears in inventory").toBeDefined();
	return workflow!;
}

test.describe("Workflow access panel", () => {
	let organizationId: string;
	let identityId: string;
	let workflowId: string;

	test.beforeAll(async ({ api }) => {
		const org = await api.post("/api/organizations", {
			data: { name: ORG_NAME, domain: `contoso-${SUFFIX}.gobifrost.dev` },
		});
		expect(org.ok(), `create organization: ${await org.text()}`).toBe(true);
		organizationId = ((await org.json()) as { id: string }).id;

		const identity = await api.post("/api/identities", {
			data: { name: IDENTITY_NAME, organization_id: organizationId },
		});
		expect(identity.ok(), `create identity: ${await identity.text()}`).toBe(
			true,
		);
		identityId = ((await identity.json()) as { id: string }).id;

		const write = await api.put("/api/files/editor/content", {
			data: {
				path: WORKFLOW_PATH,
				encoding: "utf-8",
				content: `from bifrost import workflow\n\n@workflow(name="${WORKFLOW_NAME}")\nasync def ${WORKFLOW_NAME}() -> dict:\n    return {"ok": True}\n`,
			},
		});
		expect(write.ok(), `write workflow: ${write.status()}`).toBe(true);
		const register = await api.post("/api/workflows/register", {
			data: { path: WORKFLOW_PATH, function_name: WORKFLOW_NAME },
		});
		expect(
			register.ok(),
			`register workflow: ${await register.text()}`,
		).toBe(true);
		workflowId = ((await register.json()) as { id: string }).id;
		const scoped = await api.patch(`/api/workflows/${workflowId}`, {
			data: { organization_id: organizationId },
		});
		expect(
			scoped.ok(),
			`scope workflow to ${ORG_NAME}: ${await scoped.text()}`,
		).toBe(true);
	});

	test.afterAll(async ({ api }) => {
		// The identity can't be deleted while the workflow runs as it.
		if (workflowId) {
			const reset = await api.patch(`/api/workflows/${workflowId}`, {
				data: { run_identity_id: null },
			});
			expect([200, 404]).toContain(reset.status());
		}
		const removed = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
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

	test("WORKFLOW-ACCESS-01 chooses who a workflow runs as unattended", async ({
		page,
		api,
	}) => {
		await page.goto("/workflows");
		await page
			.getByPlaceholder("Search by name, description, or category...")
			.fill(WORKFLOW_NAME);
		await page
			.getByRole("button", {
				name: `${WORKFLOW_NAME} actions`,
				exact: true,
			})
			.click();
		await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
		const dialog = page.getByRole("dialog", {
			name: "Edit Workflow Settings",
		});
		await dialog.getByRole("tab", { name: "Access", exact: true }).click();

		const runsAs = dialog.getByRole("combobox", {
			name: "Runs Unattended As",
		});
		await expect(runsAs).toContainText("Default Identity");
		await expect(runsAs).toContainText(`${ORG_NAME} · Default`);

		await runsAs.click();
		await page.getByPlaceholder("Search identities").fill(IDENTITY_NAME);
		await page.getByRole("option", { name: IDENTITY_NAME }).click();
		await expect(page.getByText("Identity changed")).toBeVisible();
		await expect(runsAs).toContainText(IDENTITY_NAME);
		await expect(runsAs).toContainText(`${ORG_NAME} · Custom`);
		expect((await readWorkflow(api, workflowId)).run_identity_id).toBe(
			identityId,
		);

		await expect(
			dialog
				.getByRole("region", { name: "Recommended Access" })
				.getByText("No Runs Observed Yet"),
		).toBeVisible();
	});
});
