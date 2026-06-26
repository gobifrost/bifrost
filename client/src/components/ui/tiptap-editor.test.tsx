import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUseEditor } = vi.hoisted(() => ({
	mockUseEditor: vi.fn(() => ({
		getMarkdown: () => "# Hello",
		commands: { setContent: vi.fn() },
		setEditable: vi.fn(),
	})),
}));

vi.mock("@tiptap/starter-kit", () => ({
	default: { configure: vi.fn(() => "starter-kit") },
}));
vi.mock("@tiptap/extension-link", () => ({
	default: { configure: vi.fn(() => "link") },
}));
vi.mock("@tiptap/extension-placeholder", () => ({
	default: { configure: vi.fn(() => "placeholder") },
}));
vi.mock("@tiptap/markdown", () => ({
	Markdown: "markdown",
}));
vi.mock("./tiptap-toolbar", () => ({
	TiptapToolbar: () => <div data-testid="toolbar" />,
}));
vi.mock("@tiptap/react", () => ({
	useEditor: mockUseEditor,
	EditorContent: ({ className }: { className?: string }) => (
		<div data-testid="editor-content" className={className}>
			<div className="tiptap">
				<h2>Hello</h2>
			</div>
		</div>
	),
}));

import { TiptapEditor } from "./tiptap-editor";

describe("TiptapEditor", () => {
	it("applies the shared TipTap editor class used by global markdown spacing rules", () => {
		render(<TiptapEditor content="# Hello" readOnly />);

		expect(mockUseEditor).toHaveBeenCalledWith(
			expect.objectContaining({
				editorProps: expect.objectContaining({
					attributes: expect.objectContaining({
						class: expect.stringContaining("tiptap-editor"),
					}),
				}),
			}),
		);
	});
});
