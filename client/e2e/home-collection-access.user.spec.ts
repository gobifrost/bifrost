import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type {
	APIResponse,
	Browser,
	BrowserContext,
	Page,
} from "@playwright/test";
import { expect, test } from "@playwright/test";

type MutatingMethod = "POST" | "PUT" | "PATCH" | "DELETE";

type Api = {
	get(url: string, options?: RequestOptions): Promise<APIResponse>;
	post(url: string, options?: RequestOptions): Promise<APIResponse>;
	put(url: string, options?: RequestOptions): Promise<APIResponse>;
	delete(url: string, options?: RequestOptions): Promise<APIResponse>;
};

type RequestOptions = {
	data?: unknown;
	params?: Record<string, string | number | boolean>;
};

type Credentials = {
	org1_user: { organizationId: string };
	org2_user: { organizationId: string };
};

type HomeCollection = {
	id: string;
	name: string;
	resource_keys: string[];
	can_edit: boolean;
	shared: boolean;
};

type HomeResponse = {
	collections: HomeCollection[];
};

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const ORG1_FORM_NAME = `Home access org1 form ${UNIQUE}`;
const ORG2_FORM_NAME = `Home access org2 form ${UNIQUE}`;
const ORG1_PRIVATE_NAME = `Home access private org1 ${UNIQUE}`;
const ORG1_PRIVATE_EDITED = `${ORG1_PRIVATE_NAME} edited`;
const ORG2_PRIVATE_NAME = `Home access private org2 ${UNIQUE}`;
const SHARED_NAME = `Home access shared global ${UNIQUE}`;

const STORAGE = {
	admin: "e2e/.auth/platform_admin.json",
	org1: "e2e/.auth/org1_user.json",
	org2: "e2e/.auth/org2_user.json",
} as const;

async function buildApi(context: BrowserContext): Promise<Api> {
	const cookies = await context.cookies();
	const csrf = cookies.find((cookie) => cookie.name === "csrf_token")?.value;
	const send = async (
		method: MutatingMethod | "GET",
		url: string,
		options: RequestOptions = {},
	) =>
		context.request.fetch(url, {
			method,
			data: options.data,
			params: options.params,
			headers:
				method === "GET" || !csrf
					? undefined
					: { "X-CSRF-Token": csrf },
		});

	return {
		get: (url, options) => send("GET", url, options),
		post: (url, options) => send("POST", url, options),
		put: (url, options) => send("PUT", url, options),
		delete: (url, options) => send("DELETE", url, options),
	};
}

async function newActor(
	browser: Browser,
	storageState: string,
): Promise<{ context: BrowserContext; page: Page; api: Api }> {
	const context = await browser.newContext({ storageState });
	const page = await context.newPage();
	const api = await buildApi(context);
	return { context, page, api };
}

async function expectOk(response: APIResponse, label: string): Promise<void> {
	expect(response.ok(), `${label}: ${await response.text()}`).toBe(true);
}

async function expectDeleted(
	response: APIResponse,
	label: string,
): Promise<void> {
	expect(
		[200, 204, 404].includes(response.status()),
		`${label}: ${response.status()}`,
	).toBe(true);
}

async function createForm(
	api: Api,
	name: string,
	organizationId: string,
): Promise<{ id: string; key: string }> {
	const response = await api.post("/api/forms", {
		data: {
			name,
			description: `Collection access fixture ${name}`,
			workflow_id: null,
			organization_id: organizationId,
			form_schema: { fields: [] },
			access_level: "authenticated",
		},
	});
	await expectOk(response, `create ${name}`);
	const form = (await response.json()) as { id: string };
	return { id: form.id, key: `form:${form.id}` };
}

async function createCollection(
	api: Api,
	data: {
		name: string;
		shared: boolean;
		organization_id: string | null;
		resource_keys: string[];
	},
): Promise<HomeCollection> {
	const response = await api.post("/api/home/collections", {
		data: {
			description: `Collection access fixture ${data.name}`,
			icon: "folder",
			...data,
		},
	});
	await expectOk(response, `create collection ${data.name}`);
	return (await response.json()) as HomeCollection;
}

async function readHome(api: Api): Promise<HomeResponse> {
	const response = await api.get("/api/home");
	await expectOk(response, "read Home");
	return (await response.json()) as HomeResponse;
}

function collectionByName(home: HomeResponse, name: string): HomeCollection {
	const collection = home.collections.find((item) => item.name === name);
	expect(collection, `Home collection ${name} exists`).toBeTruthy();
	return collection!;
}

async function openHome(page: Page): Promise<void> {
	await page.goto("/");
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
		page.getByRole("navigation", { name: "Collections" }),
	).toBeVisible();
}

async function selectCollection(page: Page, name: string): Promise<void> {
	const nav = page.getByRole("navigation", { name: "Collections" });
	const collectionButton = nav.getByRole("button", {
		name: new RegExp(`^${escapeRegExp(name)}`),
	});
	if (await collectionButton.isVisible()) {
		await collectionButton.click();
	} else {
		await nav.getByRole("button", { name: "More collections" }).click();
		await page
			.getByRole("menuitem", {
				name: new RegExp(`^${escapeRegExp(name)}`),
			})
			.click();
	}
	await expect(page.getByRole("heading", { name })).toBeVisible();
}

function escapeRegExp(value: string): string {
	return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

test("HOME-COLLECTION-ACCESS-01 proves private ownership, shared filtering, and edit authority", async ({
	browser,
}) => {
	const org1 = await newActor(browser, STORAGE.org1);
	const org2 = await newActor(browser, STORAGE.org2);
	const admin = await newActor(browser, STORAGE.admin);
	const ownedCollectionIds: Array<{ api: Api; id: string; label: string }> =
		[];
	let org1FormId: string | undefined;
	let org2FormId: string | undefined;

	try {
		const credentials = JSON.parse(
			readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
		) as Credentials;
		const org1Id = credentials.org1_user.organizationId;
		const org2Id = credentials.org2_user.organizationId;
		expect(org1Id, "org1 user has organization id").toBeTruthy();
		expect(org2Id, "org2 user has organization id").toBeTruthy();

		const org1Form = await createForm(admin.api, ORG1_FORM_NAME, org1Id);
		org1FormId = org1Form.id;
		const org2Form = await createForm(admin.api, ORG2_FORM_NAME, org2Id);
		org2FormId = org2Form.id;

		const org1Private = await createCollection(org1.api, {
			name: ORG1_PRIVATE_NAME,
			shared: false,
			organization_id: null,
			resource_keys: [org1Form.key],
		});
		ownedCollectionIds.push({
			api: org1.api,
			id: org1Private.id,
			label: "org1 private collection",
		});

		const org2Private = await createCollection(org2.api, {
			name: ORG2_PRIVATE_NAME,
			shared: false,
			organization_id: null,
			resource_keys: [org2Form.key],
		});
		ownedCollectionIds.push({
			api: org2.api,
			id: org2Private.id,
			label: "org2 private collection",
		});

		const shared = await createCollection(admin.api, {
			name: SHARED_NAME,
			shared: true,
			organization_id: null,
			resource_keys: [org1Form.key, org2Form.key],
		});
		ownedCollectionIds.push({
			api: admin.api,
			id: shared.id,
			label: "admin shared collection",
		});

		const org1Home = await readHome(org1.api);
		const ownPrivate = collectionByName(org1Home, ORG1_PRIVATE_NAME);
		expect(ownPrivate.can_edit).toBe(true);
		expect(ownPrivate.shared).toBe(false);
		expect(ownPrivate.resource_keys).toEqual([org1Form.key]);
		expect(
			org1Home.collections.some(
				(item) => item.name === ORG2_PRIVATE_NAME,
			),
		).toBe(false);

		const memberShared = collectionByName(org1Home, SHARED_NAME);
		expect(memberShared.can_edit).toBe(false);
		expect(memberShared.shared).toBe(true);
		expect(memberShared.resource_keys).toEqual([org1Form.key]);

		await openHome(org1.page);
		await expect(
			org1.page.getByRole("button", {
				name: new RegExp(`^${escapeRegExp(ORG1_PRIVATE_NAME)}`),
			}),
		).toBeVisible();
		await expect(
			org1.page.getByRole("button", {
				name: new RegExp(`^${escapeRegExp(ORG2_PRIVATE_NAME)}`),
			}),
		).toHaveCount(0);
		await selectCollection(org1.page, ORG1_PRIVATE_NAME);
		await expect(
			org1.page.getByRole("button", {
				name: `Edit ${ORG1_PRIVATE_NAME}`,
			}),
		).toBeVisible();

		await org1.page
			.getByRole("button", { name: `Edit ${ORG1_PRIVATE_NAME}` })
			.click();
		await expect(
			org1.page.getByRole("dialog", { name: "Edit collection" }),
		).toBeVisible();
		await org1.page.getByLabel("Name").fill(ORG1_PRIVATE_EDITED);
		const saveOwn = org1.page.waitForResponse(
			(response) =>
				response
					.url()
					.includes(`/api/home/collections/${org1Private.id}`) &&
				response.request().method() === "PUT",
		);
		await org1.page
			.getByRole("button", { name: "Save collection" })
			.click();
		expect((await saveOwn).ok()).toBe(true);
		await expect(
			org1.page.getByRole("heading", { name: ORG1_PRIVATE_EDITED }),
		).toBeVisible();

		await openHome(org1.page);
		await selectCollection(org1.page, SHARED_NAME);
		await expect(
			org1.page.getByRole("button", { name: `Edit ${SHARED_NAME}` }),
		).toHaveCount(0);
		await expect(
			org1.page.getByRole("button", {
				name: ORG1_FORM_NAME,
				exact: true,
			}),
		).toBeVisible();
		await expect(
			org1.page.getByRole("button", {
				name: ORG2_FORM_NAME,
				exact: true,
			}),
		).toHaveCount(0);

		const adminHome = await readHome(admin.api);
		const adminShared = collectionByName(adminHome, SHARED_NAME);
		expect(adminShared.can_edit).toBe(true);
		expect(adminShared.resource_keys).toEqual([org1Form.key, org2Form.key]);
		await openHome(admin.page);
		await selectCollection(admin.page, SHARED_NAME);
		await expect(
			admin.page.getByRole("button", { name: `Edit ${SHARED_NAME}` }),
		).toBeVisible();
	} finally {
		for (const collection of ownedCollectionIds.reverse()) {
			await expectDeleted(
				await collection.api.delete(
					`/api/home/collections/${collection.id}`,
				),
				`delete ${collection.label}`,
			);
		}
		if (org2FormId) {
			await expectDeleted(
				await admin.api.delete(`/api/forms/${org2FormId}`),
				"delete org2 form",
			);
		}
		if (org1FormId) {
			await expectDeleted(
				await admin.api.delete(`/api/forms/${org1FormId}`),
				"delete org1 form",
			);
		}
		await Promise.all([
			org1.context.close(),
			org2.context.close(),
			admin.context.close(),
		]);
	}
});
