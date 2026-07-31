import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockTiptapEditor = vi.fn(
	({
		content,
		onChange,
		readOnly,
		ariaLabel,
	}: {
		content: string;
		onChange?: (value: string) => void;
		readOnly?: boolean;
		ariaLabel?: string;
	}) => (
		<textarea
			aria-label={ariaLabel}
			value={content}
			readOnly={readOnly}
			onChange={(event) => onChange?.(event.target.value)}
		/>
	),
);

vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: (props: unknown) =>
		mockTiptapEditor(
			props as {
				content: string;
				onChange?: (value: string) => void;
				readOnly?: boolean;
				ariaLabel?: string;
			},
		),
}));

import { MarkdownEditorField } from "./markdown-editor-field";

beforeEach(() => {
	mockTiptapEditor.mockClear();
});

describe("MarkdownEditorField", () => {
	it("edits Markdown through TipTap and forwards changes", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		render(
			<MarkdownEditorField
				value="Be helpful."
				onChange={onChange}
				ariaLabel="Inline instructions"
			/>,
		);

		const editor = screen.getByRole("textbox", {
			name: "Inline instructions",
		});
		expect(editor).not.toHaveAttribute("readonly");
		await user.type(editor, " Always.");
		expect(onChange).toHaveBeenCalled();
	});

	it("toggles the same Markdown into a read-only TipTap preview", async () => {
		const user = userEvent.setup();
		render(
			<MarkdownEditorField
				value="## Triage"
				onChange={vi.fn()}
				ariaLabel="Inline instructions"
			/>,
		);

		await user.click(
			screen.getByRole("radio", { name: "Preview markdown" }),
		);

		expect(
			screen.getByRole("textbox", { name: "Inline instructions" }),
		).toHaveAttribute("readonly");
		expect(mockTiptapEditor).toHaveBeenLastCalledWith(
			expect.objectContaining({
				content: "## Triage",
				readOnly: true,
				onChange: undefined,
			}),
		);
	});

	it("renders managed Markdown as a viewer without an edit toggle", () => {
		render(
			<MarkdownEditorField
				value="Managed instructions"
				readOnly
				ariaLabel="Managed instructions"
			/>,
		);

		expect(
			screen.queryByRole("radio", { name: "Edit markdown" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("textbox", { name: "Managed instructions" }),
		).toHaveAttribute("readonly");
	});
});
