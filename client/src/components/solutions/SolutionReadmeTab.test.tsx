import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SolutionReadmeTab } from "./SolutionReadmeTab";

// TipTap mounts an async ProseMirror editor that does not reliably render plain
// text synchronously in jsdom, so we mock it to a deterministic stub that
// echoes its `content` prop and forwards edits via `onChange`.
vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: ({
		content,
		onChange,
	}: {
		content: string;
		onChange?: (c: string) => void;
		readOnly?: boolean;
	}) => (
		<textarea
			data-testid="tiptap"
			value={content}
			onChange={(e) => onChange?.(e.target.value)}
		/>
	),
}));

describe("SolutionReadmeTab", () => {
	it("renders readme content via the editor", () => {
		render(
			<SolutionReadmeTab readme="# Hello" onSave={vi.fn()} canEdit={false} />,
		);
		expect(screen.getByTestId("tiptap")).toHaveValue("# Hello");
	});

	it("shows empty state when no readme and editable", () => {
		render(<SolutionReadmeTab readme={null} onSave={vi.fn()} canEdit />);
		expect(screen.getByText(/add setup instructions/i)).toBeInTheDocument();
	});

	it("shows a muted empty state when no readme and not editable", () => {
		render(<SolutionReadmeTab readme={null} onSave={vi.fn()} canEdit={false} />);
		expect(
			screen.getByText(/no setup instructions provided/i),
		).toBeInTheDocument();
	});

	it("saves edited markdown via onSave", () => {
		const onSave = vi.fn();
		render(
			<SolutionReadmeTab readme="# Hello" onSave={onSave} canEdit />,
		);
		fireEvent.click(screen.getByRole("button", { name: /edit/i }));
		fireEvent.change(screen.getByTestId("tiptap"), {
			target: { value: "# Changed" },
		});
		fireEvent.click(screen.getByRole("button", { name: /save/i }));
		expect(onSave).toHaveBeenCalledWith("# Changed");
	});

	it("does not show an Edit button when canEdit is false", () => {
		render(
			<SolutionReadmeTab readme="# Hello" onSave={vi.fn()} canEdit={false} />,
		);
		expect(
			screen.queryByRole("button", { name: /edit/i }),
		).not.toBeInTheDocument();
	});
});
