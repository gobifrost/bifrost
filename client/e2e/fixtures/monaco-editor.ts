import { expect, type Locator, type Page } from "@playwright/test";

export function editorForPath(page: Page, path: string) {
	return page.locator(`.monaco-editor[data-uri$=${JSON.stringify(path)}]`);
}

/** Read the complete document through Monaco's normal select-all/copy commands. */
export async function readEditorContent(editor: Locator) {
	const page = editor.page();
	await expect(editor).toBeVisible();
	await page
		.context()
		.grantPermissions(["clipboard-read", "clipboard-write"]);
	await page.evaluate(() => navigator.clipboard.writeText(""));
	await editor.getByRole("textbox").focus();
	await page.keyboard.press("Control+A");
	await page.keyboard.press("Control+C");
	await expect
		.poll(() => page.evaluate(() => navigator.clipboard.readText()))
		.not.toBe("");
	// Monaco uses platform line endings when copying, independently of the model's EOL.
	return (
		await page.evaluate(() => navigator.clipboard.readText())
	).replaceAll("\r\n", "\n");
}
