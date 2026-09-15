/** Real workflow metadata persistence replaces conditional list smoke checks. */
import { randomUUID } from "node:crypto";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const suffix = randomUUID().replaceAll("-", "");
const paths: string[] = [];
let target: { id: string; name: string };
let other: { id: string; name: string };
const displayName = `Reviewed workflow ${suffix}`;
const description = `Persisted workflow description ${suffix}`;

async function seed(api: AuthedApi, kind: string) {
	const name = `acceptance_${kind}_${suffix}`;
	const path = `${name}.py`;
	paths.push(path);
	const write = await api.put("/api/files/editor/content", {
		data: {
			path,
			encoding: "utf-8",
			content: `from bifrost import workflow\n\n@workflow(name="${name}")\nasync def ${name}() -> dict:\n    return {"ok": True}\n`,
		},
	});
	expect(write.ok(), `Write workflow fixture: ${write.status()}`).toBe(true);
	const register = await api.post("/api/workflows/register", {
		data: { path, function_name: name },
	});
	expect(
		register.ok(),
		`Register workflow fixture: ${register.status()}`,
	).toBe(true);
	const { id } = await register.json();
	return await readWorkflow(api, id);
}

async function readWorkflow(api: AuthedApi, id: string) {
	const response = await api.get("/api/workflows");
	expect(response.ok()).toBe(true);
	const workflows = (await response.json()) as Array<{
		id: string;
		name: string;
	}>;
	const workflow = workflows.find((item) => item.id === id);
	expect(workflow, "Registered workflow appears in inventory").toBeDefined();
	return workflow!;
}

test.beforeAll(async ({ api }) => {
	target = await seed(api, "target");
	other = await seed(api, "other");
});
test.afterAll(async ({ api }) => {
	for (const path of paths) {
		const response = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(path)}`,
		);
		expect(
			[200, 204, 404],
			`Remove workflow fixture: ${response.status()}`,
		).toContain(response.status());
	}
});

test("WORKFLOW-METADATA-01 searches, saves and reopens workflow settings before opening execution", async ({
	page,
	api,
}) => {
	await page.goto("/workflows");
	const search = page.getByPlaceholder(
		"Search by name, description, or category...",
	);
	await search.fill(target.name);
	const actions = page.getByRole("button", {
		name: `${target.name} actions`,
		exact: true,
	});
	await expect(actions).toBeVisible();
	await expect(
		page.getByRole("button", {
			name: `${other.name} actions`,
			exact: true,
		}),
	).toBeHidden();
	await actions.click();
	await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
	const dialog = page.getByRole("dialog", { name: "Edit Workflow Settings" });
	await dialog.getByLabel("Display Name", { exact: true }).fill(displayName);
	await dialog.getByLabel("Description", { exact: true }).fill(description);
	await dialog.getByRole("tab", { name: "Execution", exact: true }).click();
	await dialog.getByLabel("Timeout (seconds)").fill("90");
	await dialog.getByRole("tab", { name: "Economics", exact: true }).click();
	await dialog.getByLabel("Time Saved (minutes per execution)").fill("7");
	await dialog.getByLabel("Value (per execution)").fill("12.5");
	await dialog.getByRole("button", { name: "Save Changes" }).click();
	await expect(dialog).toBeHidden();
	const persisted = await readWorkflow(api, target.id);
	expect(persisted).toMatchObject({
		name: target.name,
		display_name: displayName,
		description,
		timeout_seconds: 90,
		time_saved: 7,
		value: 12.5,
	});
	await page.reload();
	await search.fill(displayName);
	await expect(actions).toBeVisible();
	await actions.click();
	await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
	await expect(
		dialog.getByLabel("Display Name", { exact: true }),
	).toHaveValue(displayName);
	await expect(dialog.getByLabel("Description", { exact: true })).toHaveValue(
		description,
	);
	await dialog.getByRole("tab", { name: "Economics", exact: true }).click();
	await expect(
		dialog.getByLabel("Time Saved (minutes per execution)"),
	).toHaveValue("7");
	await expect(dialog.getByLabel("Value (per execution)")).toHaveValue(
		"12.5",
	);
	await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
	await page.getByRole("link", { name: target.name, exact: true }).click();
	await expect(page).toHaveURL(
		new RegExp(`/workflows/${target.name}/execute$`),
	);
	await expect(
		page.getByRole("button", { name: "Execute Workflow", exact: true }),
	).toBeVisible();
});
