/**
 * Apps Editor Acceptance (Admin)
 *
 * Browser-level contract for the code app editor's save and file-switching
 * path. Seeds isolated source files through the real API, edits in the real
 * Monaco editor, saves through the UI, switches files, reloads, and verifies
 * persisted source through the app file API.
 */

import { test, expect, grantWorkspaceAppPolicy } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";
import { editorForPath, readEditorContent } from "./fixtures/monaco-editor";

type ViewportCase = {
	name: "desktop" | "mobile";
	size: { width: number; height: number };
};

const VIEWPORTS: ViewportCase[] = [
	{ name: "desktop", size: { width: 1440, height: 900 } },
	{ name: "mobile", size: { width: 390, height: 844 } },
];

const UNIQUE = () => `${Date.now()}-${Math.floor(Math.random() * 10000)}`;

const layoutSource = (
	marker: string,
) => `import { Outlet } from "react-router-dom";

export default function Layout() {
	return (
		<main data-testid="layout-marker">
			${marker}
			<Outlet />
		</main>
	);
}
`;

const homeSource = (marker: string) => `export default function Home() {
	return <h1 data-testid="home-marker">${marker}</h1>;
}
`;

function writeBody(path: string, content: string) {
	return {
		path,
		content: Buffer.from(content, "utf-8").toString("base64"),
		mode: "cloud",
		location: "workspace",
		binary: true,
	};
}

async function createInlineApp(api: AuthedApi) {
	const unique = UNIQUE();
	const slug = `e2e-editor-${unique}`;
	const response = await api.post("/api/applications", {
		data: {
			name: `E2E Editor ${unique}`,
			slug,
			access_level: "authenticated",
			role_ids: [],
			app_model: "inline_v1",
		},
	});
	expect(response.ok(), await response.text()).toBe(true);
	const app = (await response.json()) as { id: string; slug: string };
	await grantWorkspaceAppPolicy(api, slug);
	return app;
}

async function writeWorkspaceFile(
	api: AuthedApi,
	slug: string,
	path: string,
	source: string,
) {
	const response = await api.post("/api/files/write", {
		data: writeBody(`apps/${slug}/${path}`, source),
	});
	expect(response.ok(), `write ${path}: ${await response.text()}`).toBe(true);
}

async function readAppFile(api: AuthedApi, appId: string, path: string) {
	const response = await api.get(
		`/api/applications/${appId}/files/${encodeURIComponent(path)}?mode=draft`,
	);
	expect(response.ok(), `read ${path}: ${await response.text()}`).toBe(true);
	return (await response.json()) as { path: string; source: string };
}

async function openFile(page: Page, viewport: ViewportCase, filename: string) {
	if (viewport.name === "mobile") {
		const showTools = page.getByRole("button", {
			name: "Show files and packages",
		});
		if (await showTools.isVisible()) {
			await showTools.click();
		}
	}
	const file = page.getByRole("button", { name: filename, exact: true });
	if (filename === "index.tsx" && !(await file.isVisible())) {
		await page.getByRole("button", { name: "pages", exact: true }).click();
	}
	await file.click();
}

async function prependEditorComment(
	page: Page,
	path: string,
	comment: string,
	source: string,
) {
	const editor = editorForPath(page, path);
	await expect(editor).toBeVisible();

	const editorInput = editor.getByRole("textbox", {
		name: "Editor content",
		exact: true,
	});
	await editorInput.focus();
	await page.keyboard.press("Control+Home");
	await page.keyboard.insertText(`// ${comment}`);
	await page.keyboard.press("Enter");
	await expect.poll(() => readEditorContent(editor)).toBe(source);
}

async function saveCurrentFile(page: Page, appId: string, path: string) {
	const saveResponse = page.waitForResponse(
		(response) =>
			response.request().method() === "PUT" &&
			response
				.url()
				.includes(
					`/api/applications/${appId}/files/${encodeURIComponent(path)}`,
				),
	);
	await page.getByRole("button", { name: /^Save$/ }).click();
	const response = await saveResponse;
	expect(response.ok(), await response.text()).toBe(true);
	await expect(page.getByText("(unsaved)")).toBeHidden();
}

for (const viewport of VIEWPORTS) {
	test.describe(`${viewport.name} app editor`, () => {
		let app: { id: string; slug: string } | undefined;
		const originalLayout = layoutSource(`layout original ${viewport.name}`);
		const originalHome = homeSource(`home original ${viewport.name}`);
		const editedHome = `// home saved ${viewport.name}\n${originalHome}`;

		// Source compilation belongs to fixture setup, independently of the
		// browser action budget. Teardown receives a live API context on failure.
		test.beforeAll(async ({ api }) => {
			app = await createInlineApp(api);
			await writeWorkspaceFile(
				api,
				app.slug,
				"_layout.tsx",
				originalLayout,
			);
			await writeWorkspaceFile(
				api,
				app.slug,
				"pages/index.tsx",
				originalHome,
			);
		});
		test.afterAll(async ({ api }) => {
			if (app) {
				const response = await api.delete(
					`/api/applications/${app.id}`,
				);
				expect([200, 204, 404]).toContain(response.status());
			}
		});

		test(
			`[APP-01] editor save/file switching persists the selected file after reload — ${viewport.name}`,
			{ tag: viewport.name === "desktop" ? "@smoke" : [] },
			async ({ page, api }) => {
				const pageErrors: string[] = [];
				page.on("pageerror", (error) => pageErrors.push(error.message));
				if (!app) throw new Error("Editor app fixture was not created");
				await page.setViewportSize(viewport.size);
				await page.goto(`/apps/${app.slug}/edit`);
				await openFile(page, viewport, "index.tsx");
				await prependEditorComment(
					page,
					"pages/index.tsx",
					`home saved ${viewport.name}`,
					editedHome,
				);
				await expect(page.getByText("(unsaved)")).toBeVisible();
				await saveCurrentFile(page, app.id, "pages/index.tsx");

				await openFile(page, viewport, "_layout.tsx");
				await expect(
					page.locator(".monaco-editor").first(),
				).toContainText(`layout original ${viewport.name}`);

				await expect(
					(await readAppFile(api, app.id, "pages/index.tsx")).source,
				).toBe(editedHome);
				await expect(
					(await readAppFile(api, app.id, "_layout.tsx")).source,
				).toBe(originalLayout);

				await page.reload();
				await openFile(page, viewport, "index.tsx");
				await expect(
					page.locator(".monaco-editor").first(),
				).toContainText(`home saved ${viewport.name}`);
				expect(pageErrors).toEqual([]);
			},
		);
	});
}
