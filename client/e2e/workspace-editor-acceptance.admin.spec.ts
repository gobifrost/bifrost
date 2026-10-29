import { type Page } from "@playwright/test";
import { readEditorContent } from "./fixtures/monaco-editor";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

function uniqueSuffix() {
	return `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

async function writeEditorFile(api: AuthedApi, path: string, content: string) {
	const response = await api.put("/api/files/editor/content", {
		data: {
			path,
			content,
			encoding: "utf-8",
			expected_etag: null,
			force_ids: null,
			force_deactivation: false,
			replacements: null,
			workflows_to_deactivate: null,
		},
	});
	expect(response.ok(), `write ${path}: ${await response.text()}`).toBe(true);
}

async function readEditorFile(api: AuthedApi, path: string) {
	const response = await api.get("/api/files/editor/content", {
		params: { path },
	});
	expect(response.ok(), `read ${path}: ${await response.text()}`).toBe(true);
	return (await response.json()) as {
		path: string;
		content: string;
		encoding: "utf-8" | "base64";
		etag: string;
	};
}

async function deleteEditorPath(api: AuthedApi, path: string) {
	const response = await api.delete("/api/files/editor", {
		params: { path },
	});
	expect([200, 204, 404]).toContain(response.status());
}

async function openGlobalEditor(page: Page) {
	await page.getByRole("button", { name: "Shell (Cmd+/)" }).click();
	await expect(
		page.getByRole("dialog", { name: "Code editor" }),
	).toBeVisible();
}

async function openWorkspaceFile(
	page: Page,
	folderName: string,
	filename: string,
) {
	const dialog = page.getByRole("dialog", { name: "Code editor" });
	await dialog.getByRole("button", { name: "Files" }).click();
	const folder = dialog.getByRole("button", {
		name: folderName,
		exact: true,
	});
	await expect(folder).toBeVisible();
	if ((await folder.getAttribute("aria-expanded")) !== "true") {
		await folder.click();
	}
	await dialog
		.getByRole("button", {
			name: new RegExp(`^${filename.replaceAll(".", "\\.")}(?:\\s|$)`),
		})
		.click();
}

async function replaceEditorContent(page: Page, content: string) {
	const editor = page
		.getByRole("dialog", { name: "Code editor" })
		.getByRole("code");
	await expect(editor).toBeVisible();

	const editorInput = editor.getByRole("textbox", {
		name: "Editor content",
		exact: true,
	});
	await editorInput.focus();
	await page.keyboard.press("Control+A");
	await page.keyboard.insertText(content);
	await expect
		.poll(() =>
			readEditorContent(
				page
					.getByRole("dialog", { name: "Code editor" })
					.getByRole("code"),
			),
		)
		.toBe(content);
}

async function expectEditorContent(page: Page, content: string) {
	await expect
		.poll(() =>
			readEditorContent(
				page
					.getByRole("dialog", { name: "Code editor" })
					.getByRole("code"),
			),
		)
		.toBe(content);
}

async function saveCurrentEditorFile(page: Page) {
	const saveResponse = page.waitForResponse(
		(response) =>
			response.request().method() === "PUT" &&
			response.url().includes("/api/files/editor/content"),
	);
	await page.getByRole("button", { name: "Save" }).click();
	const response = await saveResponse;
	expect(response.ok(), await response.text()).toBe(true);
}

test.describe("Workspace editor acceptance (admin)", () => {
	test("edits, saves, switches, and reloads global workspace files", async ({
		page,
		api,
	}) => {
		await page.addInitScript(() => {
			window.localStorage.removeItem("editor-storage");
		});

		const folderName = `e2e-workspace-editor-${uniqueSuffix()}`;
		const alphaPath = `${folderName}/alpha.txt`;
		const betaPath = `${folderName}/beta.txt`;
		const alphaOriginal = "alpha original\nshared editor fixture\n";
		const betaOriginal = "beta original\nshared editor fixture\n";
		const alphaEdited = "alpha edited through global editor\n";

		await writeEditorFile(api, alphaPath, alphaOriginal);
		await writeEditorFile(api, betaPath, betaOriginal);

		try {
			await page.goto("/");
			await openGlobalEditor(page);
			await openWorkspaceFile(page, folderName, "alpha.txt");
			await expectEditorContent(page, alphaOriginal);
			await replaceEditorContent(page, alphaEdited);
			await saveCurrentEditorFile(page);

			await openWorkspaceFile(page, folderName, "beta.txt");
			await expectEditorContent(page, betaOriginal);
			expect((await readEditorFile(api, alphaPath)).content).toBe(
				alphaEdited,
			);
			expect((await readEditorFile(api, betaPath)).content).toBe(
				betaOriginal,
			);

			await page.getByRole("button", { name: "Close editor" }).click();
			await expect(
				page.getByRole("dialog", { name: "Code editor" }),
			).toBeHidden();

			await page.reload();
			await openGlobalEditor(page);
			await openWorkspaceFile(page, folderName, "alpha.txt");
			await expectEditorContent(page, alphaEdited);
			await openWorkspaceFile(page, folderName, "beta.txt");
			await expectEditorContent(page, betaOriginal);
			expect((await readEditorFile(api, alphaPath)).content).toBe(
				alphaEdited,
			);
			expect((await readEditorFile(api, betaPath)).content).toBe(
				betaOriginal,
			);
		} finally {
			await deleteEditorPath(api, folderName);
		}
	});
});
