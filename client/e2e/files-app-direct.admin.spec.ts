/**
 * Files — App Direct SDK (Admin)
 *
 * Proves a real browser app can use the Files SDK directly:
 *   - signed PUT upload
 *   - list
 *   - read
 *   - signed GET download
 *   - delete
 *   - denied UI for a blocked user
 *   - no workflow execution is created by file operations
 */

import { test, expect } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";
import type { Browser, Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import {
	getAuthStatePath,
	getCredentialsPath,
	type UserCredentials,
} from "./fixtures/users";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-files-direct-${UNIQUE}`;
const APP_NAME = `E2E Files Direct ${UNIQUE}`;
const FILE_PREFIX = `shared/gallery/app-files/${UNIQUE}`;
const FILE_PATH = `${FILE_PREFIX}/browser.txt`;
const FILE_CONTENT = `browser-upload-${UNIQUE}`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() { return <Outlet />; }
`;

const INDEX_TSX = `import { files, useState } from "bifrost";

const path = "${FILE_PATH}";
const prefix = "${FILE_PREFIX}";
const content = "${FILE_CONTENT}";

export default function Home() {
	const [status, setStatus] = useState<string>("idle");
	const [items, setItems] = useState<string[]>([]);
	const [readText, setReadText] = useState<string>("");
	const [downloadText, setDownloadText] = useState<string>("");

	async function runAllowed() {
		try {
				setStatus("uploading");
				await files.upload(path, content, {
					location: "shared",
					contentType: "text/plain",
				});

				const listed = await files.list(prefix, { location: "shared" });
				setItems(listed.files);

				const read = await files.read(path, { location: "shared" });
				setReadText(read);

				const get = await files.signedUrl(path, { method: "GET", location: "shared" });
				const downloaded = await fetch(get.url);
				if (!downloaded.ok) throw new Error(\`download:\${downloaded.status}\`);
				setDownloadText(await downloaded.text());

				await files.delete(path, { location: "shared" });
				const afterDelete = await files.list(prefix, { location: "shared" });
				setItems(afterDelete.files);
			setStatus("done");
		} catch (e) {
			setStatus(\`error:\${(e as Error).name}:\${(e as Error).message}\`);
		}
	}

	async function runBlocked() {
		try {
				await files.signedUrl(path, {
					method: "PUT",
					location: "shared",
					contentType: "text/plain",
				});
			setStatus("unexpected-allowed");
		} catch (e) {
			setStatus(\`denied:\${(e as Error).name}\`);
		}
	}

	return (
		<div>
			<button data-testid="allowed" onClick={runAllowed}>Allowed</button>
			<button data-testid="blocked" onClick={runBlocked}>Blocked</button>
			<div data-testid="status">{status}</div>
			<div data-testid="read">{readText}</div>
			<div data-testid="download">{downloadText}</div>
			<ul data-testid="items">
				{items.map((item) => <li key={item}>{item}</li>)}
			</ul>
		</div>
	);
}
`;

function loadCredentials(): Record<string, UserCredentials> {
	const credPath = path.resolve(__dirname, getCredentialsPath());
	if (!fs.existsSync(credPath)) {
		throw new Error(
			`Credentials file not found at ${credPath}. Run setup first.`,
		);
	}
	return JSON.parse(fs.readFileSync(credPath, "utf-8"));
}

function writeBody(filePath: string, content: string) {
	return {
		path: filePath,
		content: Buffer.from(content, "utf-8").toString("base64"),
		mode: "cloud",
		location: "workspace",
		binary: true,
	};
}

function trackPageErrors(page: Page): { errors: string[] } {
	const errors: string[] = [];
	page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
	page.on("console", (msg) => {
		if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
	});
	return { errors };
}

async function putPolicy(
	api: AuthedApi,
	prefix: string,
	policies: unknown,
	location = "workspace",
	scope?: string,
) {
	const response = await api.put(
		`/api/files/policies/${encodeURIComponent(prefix)}`,
		{
			params: { location, ...(scope ? { scope } : {}) },
			data: { policies },
		},
	);
	expect(response.ok(), await response.text()).toBe(true);
}

async function openAppPage(
	browser: Browser,
	baseURL: string,
	userKey: string,
): Promise<Page> {
	const context = await browser.newContext({
		baseURL,
		storageState: path.resolve(__dirname, getAuthStatePath(userKey)),
	});
	const page = await context.newPage();
	await page.goto(`/apps/${APP_SLUG}`);
	return page;
}

test.describe("Files — App Direct SDK", () => {
	let appId: string;
	let org1UserId: string;
	let org1Id: string;

	test.beforeAll(async ({ api }) => {
			const creds = loadCredentials();
			org1UserId = creds.org1_user.userId;
			expect(creds.org1_user.organizationId).toBeTruthy();
			org1Id = creds.org1_user.organizationId!;

		const createApp = await api.post("/api/applications", {
			data: {
				name: APP_NAME,
				slug: APP_SLUG,
				app_model: "inline_v1",
				organization_id: null,
				access_level: "authenticated",
				role_ids: [],
			},
		});
		expect(createApp.ok(), await createApp.text()).toBe(true);
		appId = (await createApp.json()).id;

		await putPolicy(api, `apps/${APP_SLUG}`, {
			policies: [
				{
					name: "admin_app_source",
					actions: ["read", "write", "delete", "list"],
					when: { user: "is_platform_admin" },
				},
			],
		});

			await putPolicy(api, FILE_PREFIX, {
				policies: [
					{
						name: "admin_bypass",
					actions: ["read", "write", "delete", "list"],
					when: { user: "is_platform_admin" },
				},
				{
					name: "allowed_user",
					actions: ["read", "write", "delete", "list"],
					when: { eq: [{ user: "user_id" }, org1UserId] },
					},
				],
			}, "shared", org1Id);

		for (const [relPath, source] of [
			[`apps/${APP_SLUG}/_layout.tsx`, LAYOUT_TSX],
			[`apps/${APP_SLUG}/pages/index.tsx`, INDEX_TSX],
		] as const) {
			const write = await api.post("/api/files/write", {
				data: writeBody(relPath, source),
			});
			expect(write.ok(), `write ${relPath}: ${await write.text()}`).toBe(true);
		}

		const publish = await api.post(`/api/applications/${appId}/publish`, {
			data: { message: "seed files sdk direct test" },
		});
		expect(publish.ok(), await publish.text()).toBe(true);
	});

	test.afterAll(async ({ api }) => {
			if (appId) await api.delete(`/api/applications/${appId}`);
			await api.delete(`/api/files/policies/${encodeURIComponent(FILE_PREFIX)}`, {
				params: { location: "shared", scope: org1Id },
			});
		await api.delete(
			`/api/files/policies/${encodeURIComponent(`apps/${APP_SLUG}`)}`,
			{ params: { location: "workspace" } },
		);
	});

	test("allowed user uploads/lists/reads/downloads/deletes; blocked user is denied; no execution", async ({
		api,
		browser,
		baseURL,
	}) => {
		expect(baseURL).toBeTruthy();

		const before = await api.get("/api/executions?limit=50");
		expect(before.ok(), await before.text()).toBe(true);
		const beforeCount = (await before.json()).executions?.length ?? 0;

		const allowedPage = await openAppPage(browser, baseURL!, "org1_user");
		const allowedErrors = trackPageErrors(allowedPage);
		try {
			await expect(allowedPage.getByTestId("allowed")).toBeVisible({
				timeout: 30_000,
			});
			await allowedPage.getByTestId("allowed").click();
			await expect(allowedPage.getByTestId("status")).toHaveText("done", {
				timeout: 15_000,
			});
			await expect(allowedPage.getByTestId("read")).toHaveText(FILE_CONTENT);
			await expect(allowedPage.getByTestId("download")).toHaveText(FILE_CONTENT);
			await expect(allowedPage.getByTestId("items")).not.toContainText(
				FILE_PATH,
			);
			expect(allowedErrors.errors, allowedErrors.errors.join("\n")).toEqual([]);
		} finally {
			await allowedPage.context().close();
		}

		const blockedPage = await openAppPage(browser, baseURL!, "org2_user");
		const blockedErrors = trackPageErrors(blockedPage);
		try {
			await expect(blockedPage.getByTestId("blocked")).toBeVisible({
				timeout: 30_000,
			});
			await blockedPage.getByTestId("blocked").click();
			await expect(blockedPage.getByTestId("status")).toHaveText(
				"denied:FileAccessDeniedError",
				{ timeout: 10_000 },
			);
			const unexpected = blockedErrors.errors.filter(
				(error) => !error.includes("403 (Forbidden)"),
			);
			expect(unexpected, unexpected.join("\n")).toEqual([]);
		} finally {
			await blockedPage.context().close();
		}

		const after = await api.get("/api/executions?limit=50");
		expect(after.ok(), await after.text()).toBe(true);
		const afterCount = (await after.json()).executions?.length ?? 0;
		expect(afterCount).toBe(beforeCount);
	});
});
