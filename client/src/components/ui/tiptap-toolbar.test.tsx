import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Editor } from "@tiptap/react";

import { TiptapToolbar } from "./tiptap-toolbar";

function editorStub(): Editor {
	const chain: Record<string, ReturnType<typeof vi.fn>> = {};
	for (const name of [
		"focus",
		"undo",
		"redo",
		"toggleHeading",
		"toggleBold",
		"toggleItalic",
		"toggleStrike",
		"toggleCode",
		"setLink",
		"toggleBulletList",
		"toggleOrderedList",
		"toggleBlockquote",
		"run",
	]) {
		chain[name] = vi.fn(() => chain);
	}

	return {
		chain: () => chain,
		can: () => ({
			undo: () => true,
			redo: () => true,
		}),
		isActive: () => false,
	} as unknown as Editor;
}

describe("TiptapToolbar", () => {
	it("provides accessible names for every icon formatting control", () => {
		render(<TiptapToolbar editor={editorStub()} />);

		for (const name of [
			"Undo",
			"Redo",
			"Heading 2",
			"Heading 3",
			"Bold",
			"Italic",
			"Strikethrough",
			"Inline code",
			"Link",
			"Bulleted list",
			"Numbered list",
			"Block quote",
		]) {
			expect(
				screen.getByRole("button", { name }),
			).toBeInTheDocument();
		}
	});
});
