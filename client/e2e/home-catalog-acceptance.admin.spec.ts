import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Locator, Page } from "@playwright/test";
import {
	expect,
	grantWorkspaceAppPolicy,
	publishAppAndWait,
	test,
} from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const APP_SLUG = `home-catalog-${UNIQUE}`;
const APP_NAME = `Home catalog app ${UNIQUE}`;
const FORM_NAME = `Home catalog form ${UNIQUE}`;
const OTHER_FORM_NAME = `Home catalog other org form ${UNIQUE}`;
const WORKFLOW_PATH = `home_catalog_form_${UNIQUE.replace(/-/g, "_")}.py`;
const WORKFLOW_FN = `home_catalog_form_${UNIQUE.replace(/-/g, "_")}`;

const WORKFLOW_SOURCE = `from bifrost import workflow

@workflow(name="${WORKFLOW_FN}")
async def ${WORKFLOW_FN}(summary: str):
    return {"summary": summary}
`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";

export default function Layout() {
	return <Outlet />;
}
`;

const INDEX_TSX = `export default function Home() {
	return <h1>Home catalog launch app</h1>;
}
`;

type Credentials = {
	org1_user: { organizationId: string };
	org2_user: { organizationId: string };
};

type HomeResource = {
	key: string;
	id: string;
	kind: "app" | "form" | "agent";
	name: string;
	organization_id: string | null;
	organization_name: string;
	href: string;
};

type HomeResponse = {
	resources: HomeResource[];
};

function writeBody(path: string, content: string) {
	return {
		path,
		content: Buffer.from(content, "utf-8").toString("base64"),
		mode: "cloud",
		location: "workspace",
		binary: true,
	};
}

async function expectOk(
	response: Awaited<ReturnType<AuthedApi["get"]>>,
	label: string,
): Promise<void> {
	expect(response.ok(), `${label}: ${await response.text()}`).toBe(true);
}

async function expectDeleted(
	response: Awaited<ReturnType<AuthedApi["delete"]>>,
	label: string,
): Promise<void> {
	expect(
		[200, 204, 404].includes(response.status()),
		`${label}: ${response.status()}`,
	).toBe(true);
}

async function createPublishedApp(api: AuthedApi, organizationId: string) {
	const response = await api.post("/api/applications", {
		data: {
			name: APP_NAME,
			slug: APP_SLUG,
			description: "Searchable Home catalog app fixture",
			access_level: "authenticated",
			organization_id: organizationId,
			role_ids: [],
			app_model: "inline_v1",
		},
	});
	await expectOk(response, "create Home catalog app");
	const app = (await response.json()) as { id: string; slug: string };
	await grantWorkspaceAppPolicy(api, APP_SLUG);

	for (const [path, source] of [
		[`apps/${APP_SLUG}/_layout.tsx`, LAYOUT_TSX],
		[`apps/${APP_SLUG}/pages/index.tsx`, INDEX_TSX],
	] as const) {
		const write = await api.post("/api/files/write", {
			data: writeBody(path, source),
		});
		await expectOk(write, `write ${path}`);
	}

	await publishAppAndWait(api, app.id, "Home catalog launch fixture");
	return app;
}

async function createCatalogForm(
	api: AuthedApi,
	name: string,
	organizationId: string,
	workflowId: string | null,
): Promise<{ id: string }> {
	const created = await api.post("/api/forms", {
		data: {
			name,
			description: `Searchable fixture for ${name}`,
			workflow_id: workflowId,
			organization_id: organizationId,
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
			access_level: "authenticated",
		},
	});
	await expectOk(created, `create ${name}`);
	return (await created.json()) as { id: string };
}

async function registerWorkflow(
	api: AuthedApi,
	organizationId: string,
): Promise<string> {
	const write = await api.put("/api/files/editor/content", {
		data: {
			path: WORKFLOW_PATH,
			content: WORKFLOW_SOURCE,
			encoding: "utf-8",
		},
	});
	await expectOk(write, "write Home catalog workflow source");

	const registration = await api.post("/api/workflows/register", {
		data: {
			path: WORKFLOW_PATH,
			function_name: WORKFLOW_FN,
			organization_id: organizationId,
		},
	});
	await expectOk(registration, "register Home catalog workflow");

	const workflows = await api.get("/api/workflows", {
		params: { scope: organizationId },
	});
	await expectOk(workflows, "read Home catalog workflow");
	const workflow = (
		(await workflows.json()) as Array<{ id: string; name: string }>
	).find((item) => item.name === WORKFLOW_FN);
	expect(workflow, "registered Home catalog workflow exists").toBeTruthy();
	return workflow!.id;
}

async function readHome(api: AuthedApi): Promise<HomeResponse> {
	const response = await api.get("/api/home");
	await expectOk(response, "read Home catalog");
	return (await response.json()) as HomeResponse;
}

function findResource(
	home: HomeResponse,
	kind: HomeResource["kind"],
	name: string,
): HomeResource {
	const resource = home.resources.find(
		(item) => item.kind === kind && item.name === name,
	);
	expect(resource, `Home resource ${kind}:${name} exists`).toBeTruthy();
	return resource!;
}

async function showAllCatalog(page: Page): Promise<void> {
	await page.goto("/?catalog=all");
	await expect(
		page.getByRole("heading", {
			name: "Workspace",
			exact: true,
			level: 1,
		}),
	).toBeVisible({
		timeout: 10_000,
	});
	await expect(
		page.getByRole("region", { name: "Browse resources" }),
	).toBeVisible();
}

async function expectVisibleResource(
	page: Page,
	name: string,
): Promise<Locator> {
	const resource = page.getByRole("button", { name, exact: true });
	await expect(resource).toBeVisible();
	return resource;
}

async function expectNoVisibleResource(
	page: Page,
	name: string,
): Promise<void> {
	await expect(page.getByRole("button", { name, exact: true })).toHaveCount(
		0,
	);
}

async function chooseOrganization(page: Page, organizationName: string) {
	await page.getByRole("combobox", { name: "Organization filter" }).click();
	await page
		.locator('[data-slot="command-item"]')
		.filter({ hasText: organizationName })
		.click();
}

async function chooseResourceType(page: Page, name: string) {
	await page.getByRole("combobox", { name: "Resource type" }).click();
	await page.getByRole("option", { name }).click();
}

test("HOME-CATALOG-01 launches app/form resources and filters exact Home catalog data", async ({
	api,
	page,
}) => {
	const credentials = JSON.parse(
		readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
	) as Credentials;
	const org1Id = credentials.org1_user.organizationId;
	const org2Id = credentials.org2_user.organizationId;

	let appId: string | undefined;
	let formId: string | undefined;
	let otherFormId: string | undefined;

	try {
		const workflowId = await registerWorkflow(api, org1Id);
		const app = await createPublishedApp(api, org1Id);
		appId = app.id;
		const form = await createCatalogForm(
			api,
			FORM_NAME,
			org1Id,
			workflowId,
		);
		formId = form.id;
		const otherForm = await createCatalogForm(
			api,
			OTHER_FORM_NAME,
			org2Id,
			null,
		);
		otherFormId = otherForm.id;

		const home = await readHome(api);
		const appResource = findResource(home, "app", APP_NAME);
		const formResource = findResource(home, "form", FORM_NAME);
		const otherFormResource = findResource(home, "form", OTHER_FORM_NAME);
		expect(appResource.organization_id).toBe(org1Id);
		expect(formResource.organization_id).toBe(org1Id);
		expect(otherFormResource.organization_id).toBe(org2Id);

		await showAllCatalog(page);
		await page.getByLabel("Search Home resources").fill(APP_NAME);
		await expectVisibleResource(page, APP_NAME);
		await expectNoVisibleResource(page, FORM_NAME);
		await page.getByLabel("Search Home resources").fill(UNIQUE);
		await expectVisibleResource(page, APP_NAME);
		await expectVisibleResource(page, FORM_NAME);
		await expectVisibleResource(page, OTHER_FORM_NAME);

		await chooseResourceType(page, "Apps");
		await expectVisibleResource(page, APP_NAME);
		await expectNoVisibleResource(page, FORM_NAME);
		await expectNoVisibleResource(page, OTHER_FORM_NAME);

		await chooseResourceType(page, "Forms");
		await expectNoVisibleResource(page, APP_NAME);
		await expectVisibleResource(page, FORM_NAME);
		await expectVisibleResource(page, OTHER_FORM_NAME);

		await chooseOrganization(page, formResource.organization_name);
		await expectVisibleResource(page, FORM_NAME);
		await expectNoVisibleResource(page, OTHER_FORM_NAME);

		await chooseResourceType(page, "All types");
		await expectVisibleResource(page, APP_NAME);
		await expectVisibleResource(page, FORM_NAME);
		await expectNoVisibleResource(page, OTHER_FORM_NAME);

		await page.getByLabel("Search Home resources").fill(APP_NAME);
		await (await expectVisibleResource(page, APP_NAME)).click();
		await expect(page).toHaveURL(new RegExp(`${appResource.href}/?$`));

		await showAllCatalog(page);
		await page.getByLabel("Search Home resources").fill(FORM_NAME);
		await (await expectVisibleResource(page, FORM_NAME)).click();
		await expect(page).toHaveURL(new RegExp(`${formResource.href}$`));
	} finally {
		if (otherFormId) {
			await expectDeleted(
				await api.delete(`/api/forms/${otherFormId}`),
				"delete other-org Home form",
			);
		}
		if (formId) {
			await expectDeleted(
				await api.delete(`/api/forms/${formId}`),
				"delete Home form",
			);
		}
		if (appId) {
			await expectDeleted(
				await api.delete(`/api/applications/${appId}`),
				"delete Home app",
			);
		}
		await expectDeleted(
			await api.delete(
				`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
			),
			"delete Home workflow source",
		);
	}
});
