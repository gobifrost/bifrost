import { randomUUID } from "node:crypto";
import { expect, test, type AuthedApi } from "./fixtures/api-fixture";

const suffix = randomUUID().replaceAll("-", "");
const workflowName = `e2e_event_source_management_${suffix}`;
const workflowPath = `${workflowName}.py`;
const sourceName = `E2E Event Source Management ${suffix}`;
const editedSourceName = `${sourceName} edited`;
const eventType = `event.management.${suffix}`;

type EventSourceResponse = {
	id: string;
	name: string;
	is_active: boolean;
	organization_id: string | null;
	subscription_count: number;
	webhook?: {
		adapter_name: string | null;
		config: Record<string, unknown>;
		rate_limit_per_minute: number | null;
		rate_limit_window_seconds: number;
		rate_limit_enabled: boolean;
		secret_set: boolean;
	} | null;
};

type EventSubscriptionResponse = {
	id: string;
	target_type: string;
	workflow_id: string | null;
	workflow_name: string | null;
	event_type: string | null;
	is_active: boolean;
};

let workflowId: string | undefined;
let sourceId: string | undefined;

async function expectOk(response: Awaited<ReturnType<AuthedApi["get"]>>) {
	expect(response.ok(), `${response.status()} ${await response.text()}`).toBe(
		true,
	);
}

async function registerWorkflow(api: AuthedApi) {
	const write = await api.put("/api/files/editor/content", {
		data: {
			path: workflowPath,
			encoding: "utf-8",
			content: `from bifrost import workflow

@workflow(name="${workflowName}", description="E2E event source management fixture")
async def ${workflowName}() -> dict:
    return {"ok": True}
`,
		},
	});
	await expectOk(write);

	const register = await api.post("/api/workflows/register", {
		data: { path: workflowPath, function_name: workflowName },
	});
	await expectOk(register);
	const workflow = (await register.json()) as { id: string };
	workflowId = workflow.id;
}

async function readSource(api: AuthedApi, id: string) {
	const response = await api.get(`/api/events/sources/${id}`);
	await expectOk(response);
	return (await response.json()) as EventSourceResponse;
}

async function readSubscriptions(api: AuthedApi, id: string) {
	const response = await api.get(`/api/events/sources/${id}/subscriptions`);
	await expectOk(response);
	return (await response.json()) as {
		items: EventSubscriptionResponse[];
		total: number;
	};
}

test.beforeAll(async ({ api }) => {
	await registerWorkflow(api);
});

test.afterAll(async ({ api }) => {
	if (sourceId) {
		const response = await api.delete(`/api/events/sources/${sourceId}`);
		expect(
			[200, 204, 404],
			`Delete event source: ${response.status()}`,
		).toContain(response.status());
	}

	const deleteWorkflowFile = await api.delete(
		`/api/files/editor?path=${encodeURIComponent(workflowPath)}`,
	);
	expect(
		[200, 204, 404],
		`Delete workflow fixture: ${deleteWorkflowFile.status()}`,
	).toContain(deleteWorkflowFile.status());
});

test("[EVENT-MGMT-01 desktop] creates, edits, persists, deactivates, and deletes a generic webhook source with a workflow subscription", async ({
	page,
	api,
}) => {
	expect(workflowId).toBeTruthy();
	await page.setViewportSize({ width: 1440, height: 900 });

	await page.goto("/event-sources");
	await expect(
		page.getByRole("heading", { level: 1, name: "Event Sources", exact: true }),
	).toBeVisible({ timeout: 10_000 });
	// The header action precedes the equivalent empty-state action on an empty list.
	await page
		.getByRole("button", { name: "Create event source" })
		.first()
		.click();

	const createDialog = page.getByRole("dialog", {
		name: "Create Event Source",
	});
	await expect(createDialog).toBeVisible();
	await createDialog.getByLabel("Name").fill(sourceName);
	await createDialog
		.getByRole("combobox", { name: "Webhook Adapter" })
		.click();
	await page.getByRole("option", { name: "Generic Webhook" }).click();
	await createDialog.getByRole("button", { name: "Advanced" }).click();
	await createDialog.getByLabel("Max events").fill("45");
	await createDialog.getByLabel("Per (seconds)").fill("30");
	await createDialog
		.getByRole("button", { name: "Create Event Source" })
		.click();
	await expect(createDialog).toBeHidden({ timeout: 10_000 });

	await page.getByLabel("Search event sources...").fill(sourceName);
	await page.getByRole("link", { name: sourceName }).click();
	await expect(
		page.getByRole("heading", { level: 1, name: sourceName }),
	).toBeVisible({ timeout: 10_000 });

	const createdUrl = new URL(page.url());
	sourceId = createdUrl.pathname.split("/").filter(Boolean).at(-1);
	expect(sourceId).toBeTruthy();
	const createdSource = await readSource(api, sourceId!);
	expect(createdSource).toMatchObject({
		id: sourceId,
		name: sourceName,
		is_active: true,
		subscription_count: 0,
	});
	expect(createdSource.webhook).toMatchObject({
		adapter_name: "generic",
		rate_limit_per_minute: 45,
		rate_limit_window_seconds: 30,
		rate_limit_enabled: true,
	});

	await page.getByRole("tab", { name: "Subscriptions" }).click();
	await page
		.getByRole("button", { name: "Add Subscription" })
		.first()
		.click();
	const subscriptionDialog = page.getByRole("dialog", {
		name: "Add Subscription",
	});
	await subscriptionDialog
		.getByRole("button", { name: "Select a workflow..." })
		.click();
	const workflowDialog = page.getByRole("dialog", {
		name: "Select Workflow",
	});
	await workflowDialog
		.getByPlaceholder("Search workflows...")
		.fill(workflowName);
	await workflowDialog
		.getByRole("button", { name: new RegExp(workflowName) })
		.click();
	await workflowDialog.getByRole("button", { name: "Select" }).click();
	await expect(workflowDialog).toBeHidden();
	await expect(
		subscriptionDialog.getByRole("button", {
			name: `Workflow: ${workflowName}`,
		}),
	).toBeVisible();
	await subscriptionDialog
		.getByLabel("Event Type Filter (optional)")
		.fill(eventType);
	await subscriptionDialog
		.getByRole("button", { name: "Add Subscription" })
		.click();
	await expect(subscriptionDialog).toBeHidden({ timeout: 10_000 });
	await expect(
		page.getByRole("button", {
			name: `Edit subscription for ${workflowName}`,
			exact: true,
		}),
	).toBeVisible({
		timeout: 10_000,
	});
	await expect(
		page.getByRole("cell", { name: eventType, exact: true }),
	).toBeVisible();

	const subscriptions = await readSubscriptions(api, sourceId!);
	expect(subscriptions.total).toBe(1);
	expect(subscriptions.items[0]).toMatchObject({
		target_type: "workflow",
		workflow_id: workflowId,
		workflow_name: workflowName,
		event_type: eventType,
		is_active: true,
	});
	expect((await readSource(api, sourceId!)).subscription_count).toBe(1);

	await page.getByRole("button", { name: `${sourceName} actions` }).click();
	await page.getByRole("menuitem", { name: "Edit" }).click();
	const editDialog = page.getByRole("dialog", { name: "Edit Event Source" });
	await editDialog.getByLabel("Name").fill(editedSourceName);
	await editDialog.getByLabel("Max events").fill("12");
	await editDialog.getByLabel("Per (seconds)").fill("15");
	await editDialog.getByRole("switch", { name: "Enabled" }).click();
	await editDialog.getByRole("button", { name: "Save Changes" }).click();
	await expect(editDialog).toBeHidden({ timeout: 10_000 });
	await expect(
		page.getByRole("heading", { level: 1, name: editedSourceName }),
	).toBeVisible({ timeout: 10_000 });

	const editedSource = await readSource(api, sourceId!);
	expect(editedSource.name).toBe(editedSourceName);
	expect(editedSource.webhook).toMatchObject({
		rate_limit_per_minute: 12,
		rate_limit_window_seconds: 15,
		rate_limit_enabled: false,
	});

	await page.reload();
	await expect(
		page.getByRole("heading", { level: 1, name: editedSourceName }),
	).toBeVisible({ timeout: 10_000 });
	await expect(page.getByText("1 subscription")).toBeVisible();
	await page.getByRole("tab", { name: "Subscriptions" }).click();
	await expect(
		page.getByRole("button", {
			name: `Edit subscription for ${workflowName}`,
			exact: true,
		}),
	).toBeVisible();
	await expect(
		page.getByRole("cell", { name: eventType, exact: true }),
	).toBeVisible();

	await page
		.getByRole("button", { name: `${editedSourceName} actions` })
		.click();
	await page.getByRole("menuitem", { name: "Rotate signing secret" }).click();
	const rotateDialog = page.getByRole("alertdialog", {
		name: "Rotate signing secret?",
	});
	await rotateDialog.getByRole("button", { name: "Rotate secret" }).click();
	const reveal = page.getByRole("region", {
		name: "New webhook signing secret",
	});
	const revealed = (await reveal.getByLabel("Secret value").textContent()) ?? "";
	expect(revealed.length).toBeGreaterThanOrEqual(32);
	const rotatedRead = await api.get(`/api/events/sources/${sourceId!}`);
	await expectOk(rotatedRead);
	expect(await rotatedRead.text()).not.toContain(revealed);
	expect(((await rotatedRead.json()) as EventSourceResponse).webhook).toMatchObject({
		secret_set: true,
	});
	await reveal.getByRole("button", { name: "Dismiss secret" }).click();
	await expect(reveal).toBeHidden();

	await page.getByRole("switch", { name: "Source active" }).click();
	await expect(page.getByText("Inactive", { exact: true })).toBeVisible({
		timeout: 10_000,
	});
	expect((await readSource(api, sourceId!)).is_active).toBe(false);

	await page
		.getByRole("button", { name: `${editedSourceName} actions` })
		.click();
	await page.getByRole("menuitem", { name: "Delete" }).click();
	const deleteDialog = page.getByRole("alertdialog", {
		name: "Delete Event Source",
	});
	await expect(deleteDialog).toBeVisible();
	await deleteDialog.getByRole("button", { name: "Delete" }).click();
	await expect(page).toHaveURL(/\/event-sources$/);
	await page.getByLabel("Search event sources...").fill(editedSourceName);
	await expect(
		page.getByText("No event sources match your filters"),
	).toBeVisible({
		timeout: 10_000,
	});
	const deleted = await api.get(`/api/events/sources/${sourceId!}`);
	expect(deleted.status()).toBe(404);
	sourceId = undefined;
});
