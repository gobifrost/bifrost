import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import { Markdown } from "@tiptap/markdown";
import { TiptapToolbar } from "./tiptap-toolbar";

let editor: Editor;
afterEach(() => {
	cleanup();
	editor?.destroy();
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

function setup() {
	editor = new Editor({
		extensions: [StarterKit.configure({ link: false }), Link, Markdown],
		content:
			"[Open form](/execute/example) with `inline code`.\n\n- Parent\n  - Child",
		contentType: "markdown",
	});
	editor.commands.setTextSelection(2);
	render(<TiptapToolbar editor={editor} />);
	return userEvent.setup();
}

describe("TiptapToolbar links", () => {
	it("prefills and updates the whole current link while preserving adjacent Markdown", async () => {
		const user = setup();
		const prompt = vi.fn().mockReturnValue("/execute/updated");
		vi.stubGlobal("prompt", prompt);
		await user.click(screen.getByRole("button", { name: "Insert link" }));
		expect(prompt).toHaveBeenCalledWith(
			"Enter URL (leave empty to remove link):",
			"/execute/example",
		);
		const markdown = editor.getMarkdown();
		expect(markdown).toContain("[Open form](/execute/updated)");
		expect(markdown).toContain("`inline code`");
		expect(markdown).toMatch(/- Parent\n\s+- Child/);
		editor.commands.setContent(markdown, { contentType: "markdown" });
		expect(editor.getMarkdown()).toBe(markdown);
	});

	it("still inserts a link on selected plain text", async () => {
		const user = setup();
		editor.commands.setContent("Plain text", { contentType: "markdown" });
		editor.commands.setTextSelection({ from: 1, to: 6 });
		const prompt = vi.fn().mockReturnValue("https://example.com");
		vi.stubGlobal("prompt", prompt);
		await user.click(screen.getByRole("button", { name: "Insert link" }));
		expect(prompt).toHaveBeenCalledWith(
			"Enter URL (leave empty to remove link):",
			"",
		);
		expect(editor.getMarkdown()).toBe("[Plain](https://example.com) text");
	});

	it("removes the current link without deleting its text", async () => {
		const user = setup();
		vi.stubGlobal("prompt", vi.fn().mockReturnValue(""));
		await user.click(screen.getByRole("button", { name: "Insert link" }));
		expect(editor.getMarkdown()).toContain("Open form with `inline code`");
		expect(editor.getMarkdown()).not.toContain("/execute/example");
	});

	it("leaves the document unchanged when the prompt is cancelled", async () => {
		const user = setup();
		const original = editor.getMarkdown();
		vi.stubGlobal("prompt", vi.fn().mockReturnValue(null));
		await user.click(screen.getByRole("button", { name: "Insert link" }));
		expect(editor.getMarkdown()).toBe(original);
	});
});

describe("TiptapToolbar", () => {
	it("gives each icon control an accessible name and touch-sized hit area", () => {
		setup();

		expect(screen.getByRole("group")).toHaveAttribute(
			"aria-label",
			"Rich text formatting",
		);
		expect(screen.getByRole("button", { name: "Undo" })).toHaveClass(
			"h-11",
			"w-11",
			"sm:h-10",
			"sm:w-10",
		);
		expect(screen.getByRole("button", { name: "Redo" })).toHaveClass(
			"h-11",
			"w-11",
		);
		expect(
			screen.getByRole("button", { name: "Heading level 2" }),
		).toHaveClass("h-11", "w-11");
		expect(screen.getByRole("button", { name: "Insert link" })).toHaveClass(
			"h-11",
			"w-11",
		);
		expect(screen.getByRole("button", { name: "Block quote" })).toHaveClass(
			"h-11",
			"w-11",
		);
	});
});
