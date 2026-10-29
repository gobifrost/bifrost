/**
 * Knowledge Acceptance (Admin)
 *
 * Primary persisted journey for Knowledge: configure local fixture embeddings,
 * create a document through the UI with Global chosen explicitly, find it
 * through search, read the stored content from the document drawer, delete it
 * through the UI, and restore the previous embedding configuration.
 */

import { expect, test } from "./fixtures/api-fixture";
import type { APIResponse, Page } from "@playwright/test";
import type { AuthedApi } from "./fixtures/api-fixture";

type EmbeddingConfig = {
	connection_id: string | null;
	model: string;
	is_configured: boolean;
};

type KnowledgeDocument = {
	id: string;
	namespace: string;
	key: string | null;
	content: string;
	organization_id: string | null;
};

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const NAMESPACE = `e2e-knowledge-${UNIQUE}`;
const DOCUMENT_KEY = `knowledge-primary-${UNIQUE}`;
const SENTINEL = `northwind-knowledge-sentinel-${UNIQUE}`;
const DOCUMENT_CONTENT = [
	`# ${DOCUMENT_KEY}`,
	"",
	`Use ${SENTINEL} when validating the Knowledge acceptance journey.`,
	"This content must be persisted, searchable, readable, and deleted through the UI.",
].join("\n");
const FIXTURE_EMBEDDING_MODEL = "fixture-embedding";

let createdDocument: KnowledgeDocument | null = null;
let fixtureConnectionId: string | null = null;

async function expectApiStatus(response: APIResponse, label: string) {
	if (!response.ok()) {
		expect(
			response.ok(),
			`${label}: ${response.status()} ${await response.text()}`,
		).toBe(true);
	}
}

async function readEmbeddingConfig(api: AuthedApi): Promise<EmbeddingConfig> {
	const response = await api.get("/api/admin/llm/embedding-config");
	await expectApiStatus(response, "read embedding config");
	return (await response.json()) as EmbeddingConfig;
}

async function createFixtureEmbeddingConnection(api: AuthedApi) {
	const response = await api.post("/api/admin/ai/connections", {
		data: {
			name: `Knowledge E2E Fixture Embeddings ${UNIQUE}`,
			provider: "openai_compatible",
			api_key: "fixture-key",
			endpoint: "http://scheduler-fixtures:8080/v1",
		},
	});
	await expectApiStatus(response, "create fixture embedding connection");
	const connection = (await response.json()) as { id: string };
	fixtureConnectionId = connection.id;
	return connection.id;
}

async function configureEmbeddings(api: AuthedApi, connectionId: string) {
	const response = await api.post("/api/admin/llm/embedding-config", {
		data: {
			connection_id: connectionId,
			model: FIXTURE_EMBEDDING_MODEL,
			confirm_reindex: true,
		},
	});
	await expectApiStatus(response, "configure fixture embeddings");
	const body = (await response.json()) as { saved: boolean };
	expect(body.saved, "fixture embedding config should be saved").toBe(true);
}

async function restoreEmbeddings(api: AuthedApi, previous: EmbeddingConfig) {
	if (previous.is_configured && previous.connection_id) {
		const response = await api.post("/api/admin/llm/embedding-config", {
			data: {
				connection_id: previous.connection_id,
				model: previous.model,
				confirm_reindex: true,
			},
		});
		await expectApiStatus(response, "restore embedding config");
		return;
	}

	const response = await api.delete("/api/admin/llm/embedding-config");
	expect(
		[204, 404],
		`clear embedding config: ${response.status()} ${await response.text()}`,
	).toContain(response.status());
}

async function deleteFixtureConnection(api: AuthedApi) {
	if (!fixtureConnectionId) return;
	const response = await api.delete(
		`/api/admin/ai/connections/${fixtureConnectionId}`,
	);
	expect(
		[204, 404],
		`delete fixture embedding connection: ${response.status()} ${await response.text()}`,
	).toContain(response.status());
	fixtureConnectionId = null;
}

async function deleteCreatedDocument(api: AuthedApi) {
	if (!createdDocument) return;
	const response = await api.delete(
		`/api/knowledge-sources/${encodeURIComponent(createdDocument.namespace)}/documents/${createdDocument.id}`,
	);
	expect(
		[200, 204, 404],
		`delete knowledge document fixture: ${response.status()} ${await response.text()}`,
	).toContain(response.status());
	createdDocument = null;
}

async function fillDocumentContent(page: Page, content: string) {
	const editor = page.getByRole("textbox", { name: "Document content" });
	await expect(editor).toBeVisible({ timeout: 10000 });
	await editor.click();
	await page.keyboard.press(
		process.platform === "darwin" ? "Meta+A" : "Control+A",
	);
	await page.keyboard.insertText(content);
}

async function waitForKnowledgeDocumentCreate(page: Page, api: AuthedApi) {
	const response = await page.waitForResponse((response) => {
		const url = new URL(response.url());
		return (
			response.request().method() === "POST" &&
			url.pathname ===
				`/api/knowledge-sources/${encodeURIComponent(NAMESPACE)}/documents`
		);
	});
	if (!response.ok()) {
		expect(
			response.ok(),
			`create knowledge document: ${response.status()} ${await response.text()}`,
		).toBe(true);
	}
	// Read persisted data independently of the browser's response-body capture.
	const list = await api.get(
		`/api/knowledge-sources/${encodeURIComponent(NAMESPACE)}/documents`,
	);
	await expectApiStatus(list, "list created knowledge document");
	const records = (await list.json()) as Array<{ id: string; key: string }>;
	const record = records.find((item) => item.key === DOCUMENT_KEY);
	expect(record, "created document exists in owned namespace").toBeDefined();
	const stored = await api.get(
		`/api/knowledge-sources/${encodeURIComponent(NAMESPACE)}/documents/${record!.id}`,
	);
	await expectApiStatus(stored, "read persisted knowledge document");
	createdDocument = (await stored.json()) as KnowledgeDocument;
	expect(createdDocument).toMatchObject({
		namespace: NAMESPACE,
		key: DOCUMENT_KEY,
		organization_id: null,
	});
	// Rich-text editing serializes paragraph spacing; compare the saved words.
	expect(createdDocument.content.replace(/\s+/g, " ").trim()).toBe(
		DOCUMENT_CONTENT.replace(/\s+/g, " ").trim(),
	);
}

test.describe("Knowledge acceptance", () => {
	test("admin creates, searches, reads, and deletes a knowledge document", async ({
		api,
		page,
	}) => {
		const previousEmbedding = await readEmbeddingConfig(api);

		try {
			const connectionId = await createFixtureEmbeddingConnection(api);
			await configureEmbeddings(api, connectionId);

			await page.goto("/knowledge");
			await expect(
				page.getByRole("heading", { name: "Knowledge" }),
			).toBeVisible({ timeout: 15000 });

			await page
				.getByRole("button", { name: "Add Document" })
				.last()
				.click();
			const drawer = page.getByRole("region", {
				name: "New Document",
				exact: true,
			});
			await expect(drawer).toBeVisible({ timeout: 10000 });
			// A new document starts in the caller's own organization; a global
			// document is an explicit choice.
			await drawer
				.getByRole("combobox", { name: "Organization scope" })
				.click();
			await page.getByRole("option", { name: /^Global/ }).click();
			await expect(
				drawer.getByRole("combobox", { name: "Organization scope" }),
			).toHaveText(/Global/);
			await drawer.getByLabel("Namespace").fill(NAMESPACE);
			await drawer.getByLabel("Key (optional)").fill(DOCUMENT_KEY);
			await fillDocumentContent(page, DOCUMENT_CONTENT);
			const createResponse = waitForKnowledgeDocumentCreate(page, api);
			await drawer.getByRole("button", { name: "Save" }).click();
			await createResponse;
			await expect(drawer).toBeHidden({ timeout: 10000 });

			await page
				.getByRole("textbox", { name: "Search documents" })
				.fill(SENTINEL);
			await expect(
				page.getByRole("button", { name: DOCUMENT_KEY, exact: true }),
			).toBeVisible({
				timeout: 10000,
			});

			await page
				.getByRole("button", { name: DOCUMENT_KEY, exact: true })
				.click();
			const readDrawer = page.getByRole("region", {
				name: DOCUMENT_KEY,
				exact: true,
			});
			await expect(readDrawer).toBeVisible({ timeout: 10000 });
			await expect(
				readDrawer.getByRole("textbox", { name: "Document content" }),
			).toContainText(SENTINEL);
			await readDrawer.getByRole("button", { name: "Cancel" }).click();
			await expect(readDrawer).toBeHidden({ timeout: 10000 });

			await page
				.getByRole("button", {
					name: `More actions for ${DOCUMENT_KEY}`,
				})
				.click();
			await page.getByRole("menuitem", { name: "Delete" }).click();
			const deleteDialog = page.getByRole("alertdialog", {
				name: "Delete Document?",
			});
			await expect(deleteDialog).toBeVisible();
			await deleteDialog.getByRole("button", { name: "Delete" }).click();
			await expect(deleteDialog).toBeHidden({ timeout: 10000 });

			await page.reload();
			await page
				.getByRole("textbox", { name: "Search documents" })
				.fill(SENTINEL);
			await expect(
				page.getByRole("button", { name: DOCUMENT_KEY, exact: true }),
			).toBeHidden({
				timeout: 10000,
			});
			const deletedRead = await api.get(
				`/api/knowledge-sources/${encodeURIComponent(NAMESPACE)}/documents/${createdDocument?.id}`,
			);
			expect(
				[404],
				`knowledge document should be deleted: ${deletedRead.status()} ${await deletedRead.text()}`,
			).toContain(deletedRead.status());
			createdDocument = null;
		} finally {
			await deleteCreatedDocument(api);
			await restoreEmbeddings(api, previousEmbedding);
			await deleteFixtureConnection(api);
		}
	});
});
