import { lazy, StrictMode, Suspense, useState } from "react";
import {
	act,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Editor } from "@tiptap/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let liveEditor: Editor | null;
vi.mock("@tiptap/react", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@tiptap/react")>();
	return {
		...actual,
		useEditor: (...args: Parameters<typeof actual.useEditor>) => {
			liveEditor = actual.useEditor(...args);
			return liveEditor;
		},
	};
});

import { TiptapEditor } from "./tiptap-editor";

const markdown =
	"\n## Setup\n\n[Open form](/execute/example) with `inline code`.\n\n* Parent\n  * Child\n\n";
const unsupported =
	"## Setup\n\n<!-- Keep this comment -->\n\n| Key | Value |\n| --- | --- |\n| api | endpoint |";

function Draft({
	initial,
	onSave,
}: {
	initial: string;
	onSave: (value: string) => void;
}) {
	const [content, setContent] = useState(initial);
	return (
		<>
			<TiptapEditor
				content={content}
				onChange={setContent}
				ariaLabel="Notes"
			/>
			<button onClick={() => onSave(content)}>Save</button>
		</>
	);
}

async function ready() {
	await waitFor(() =>
		expect(screen.getByRole("textbox", { name: "Notes" })).toBeVisible(),
	);
}

async function mode(user: ReturnType<typeof userEvent.setup>, name: string) {
	await user.click(screen.getByRole("radio", { name }));
}

beforeEach(() => {
	liveEditor = null;
});

describe("TiptapEditor", () => {
	it("mounts safely when a lazy consumer commits under StrictMode", async () => {
		const LazyEditor = lazy(async () => ({ default: TiptapEditor }));
		const change = vi.fn();
		render(
			<StrictMode>
				<Suspense fallback={<p>Loading</p>}>
					<LazyEditor
						content={markdown}
						ariaLabel="Notes"
						onChange={change}
					/>
				</Suspense>
			</StrictMode>,
		);
		await ready();
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveAttribute(
			"contenteditable",
			"true",
		);
		expect(change).not.toHaveBeenCalled();
	});

	it("preserves exact Markdown through repeated mode switches and unchanged saves", async () => {
		const user = userEvent.setup();
		const save = vi.fn();
		render(<Draft initial={markdown} onSave={save} />);
		await ready();
		expect(screen.queryByRole("status")).not.toBeInTheDocument();
		for (let i = 0; i < 2; i++) {
			await mode(user, "Markdown");
			expect(screen.getByRole("textbox", { name: "Notes" })).toHaveValue(
				markdown,
			);
			await mode(user, "Visual");
		}
		await user.click(screen.getByRole("button", { name: "Save" }));
		expect(save).toHaveBeenCalledWith(markdown);
	});

	it("emits source edits directly, including unsupported syntax, without loading them into Tiptap", async () => {
		const user = userEvent.setup();
		const change = vi.fn();
		render(
			<TiptapEditor
				content={markdown}
				onChange={change}
				ariaLabel="Notes"
			/>,
		);
		await ready();
		const parse = vi.spyOn(liveEditor!.markdown!, "parse");
		await mode(user, "Markdown");
		fireEvent.change(screen.getByRole("textbox", { name: "Notes" }), {
			target: { value: unsupported },
		});
		expect(change).toHaveBeenCalledExactlyOnceWith(unsupported);
		expect(parse).not.toHaveBeenCalled();
		await mode(user, "Visual");
		expect(screen.getByRole("status")).toHaveTextContent(
			"Edit in Markdown mode",
		);
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveAttribute(
			"contenteditable",
			"false",
		);
		expect(
			screen.queryByRole("group", { name: "Rich text formatting" }),
		).not.toBeInTheDocument();
		await mode(user, "Markdown");
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveValue(
			unsupported,
		);
		expect(change).toHaveBeenCalledTimes(1);
	});

	it.each([
		"<!-- Keep this comment -->\n\nText",
		"| Key | Value |\n| --- | --- |\n| api | endpoint |",
		"![Diagram](/diagram.png)",
		"- [x] Done\n- [ ] To do",
		"<details><summary>Details</summary>Hidden</details>",
		"[unused]: /keep-definition\n\nText",
	])("protects actual parser-unsupported source: %s", async (initial) => {
		const user = userEvent.setup();
		const save = vi.fn();
		render(<Draft initial={initial} onSave={save} />);
		await ready();
		expect(screen.getByRole("status")).toHaveTextContent(
			"Edit in Markdown mode",
		);
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveAttribute(
			"aria-readonly",
			"true",
		);
		await user.click(screen.getByRole("button", { name: "Save" }));
		expect(save).toHaveBeenCalledWith(initial);
		await mode(user, "Markdown");
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveValue(
			initial,
		);
	});

	it("saves source edits after a visual preview with all unsupported content intact", async () => {
		const user = userEvent.setup();
		const save = vi.fn();
		render(<Draft initial={unsupported} onSave={save} />);
		await ready();
		await mode(user, "Markdown");
		const edited = `${unsupported}\n\n[Next](/execute/next) with \`code\`.\n`;
		fireEvent.change(screen.getByRole("textbox", { name: "Notes" }), {
			target: { value: edited },
		});
		await mode(user, "Visual");
		await user.click(screen.getByRole("button", { name: "Save" }));
		expect(save).toHaveBeenCalledWith(edited);
	});

	it("serializes real visual edits while retaining relative links, inline code, and nested lists", async () => {
		const user = userEvent.setup();
		const save = vi.fn();
		render(<Draft initial={markdown} onSave={save} />);
		await ready();
		const parse = vi.spyOn(liveEditor!.markdown!, "parse");
		act(() => {
			liveEditor!.commands.insertContentAt(1, "Updated ");
		});
		await mode(user, "Markdown");
		const value = (
			screen.getByRole("textbox", {
				name: "Notes",
			}) as HTMLTextAreaElement
		).value;
		expect(value).toContain("Updated Setup");
		expect(value).toContain("[Open form](/execute/example)");
		expect(value).toContain("`inline code`");
		expect(value).toMatch(/- Parent\n\s+- Child/);
		await user.click(screen.getByRole("button", { name: "Save" }));
		expect(save).toHaveBeenCalledWith(value);
		expect(parse).not.toHaveBeenCalled();
	});

	it("loads source edits for subsequent visual editing without emitting a conversion", async () => {
		const user = userEvent.setup();
		const change = vi.fn();
		render(
			<TiptapEditor content="Old" onChange={change} ariaLabel="Notes" />,
		);
		await ready();
		await mode(user, "Markdown");
		fireEvent.change(screen.getByRole("textbox", { name: "Notes" }), {
			target: { value: "## New\n\n`code`" },
		});
		await mode(user, "Visual");
		expect(screen.getByRole("heading", { name: "New" })).toBeVisible();
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveAttribute(
			"contenteditable",
			"true",
		);
		expect(change).toHaveBeenCalledExactlyOnceWith("## New\n\n`code`");
		act(() => {
			liveEditor!.commands.insertContentAt(1, "Visual ");
		});
		expect(change).toHaveBeenLastCalledWith("## Visual New\n\n`code`");
	});

	it.each(["Visual", "Markdown"])(
		"accepts external updates in %s mode without emitting changes",
		async (selectedMode) => {
			const user = userEvent.setup();
			const change = vi.fn();
			const { rerender } = render(
				<TiptapEditor
					content="Initial"
					onChange={change}
					ariaLabel="Notes"
				/>,
			);
			await ready();
			if (selectedMode === "Markdown") await mode(user, "Markdown");
			rerender(
				<TiptapEditor
					content={unsupported}
					onChange={change}
					ariaLabel="Notes"
				/>,
			);
			if (selectedMode === "Visual") {
				expect(screen.getByRole("status")).toBeVisible();
				await mode(user, "Markdown");
			}
			expect(screen.getByRole("textbox", { name: "Notes" })).toHaveValue(
				unsupported,
			);
			rerender(
				<TiptapEditor
					content="## Replacement"
					onChange={change}
					ariaLabel="Notes"
				/>,
			);
			expect(screen.getByRole("textbox", { name: "Notes" })).toHaveValue(
				"## Replacement",
			);
			await mode(user, "Visual");
			expect(screen.queryByRole("status")).not.toBeInTheDocument();
			expect(
				screen.getByRole("heading", { name: "Replacement" }),
			).toBeVisible();
			expect(change).not.toHaveBeenCalled();
		},
	);

	it("retains field accessibility and blur callbacks in both modes", async () => {
		const user = userEvent.setup();
		const blur = vi.fn();
		render(
			<>
				<label htmlFor="notes">Notes</label>
				<p id="help">Help</p>
				<TiptapEditor
					content="Hello"
					id="notes"
					ariaLabel="Notes"
					aria-describedby="help"
					aria-invalid
					onBlur={blur}
				/>
			</>,
		);
		await ready();
		for (const selectedMode of ["Visual", "Markdown"]) {
			if (selectedMode === "Markdown") await mode(user, "Markdown");
			const field = screen.getByRole("textbox", { name: "Notes" });
			expect(field).toHaveAttribute("id", "notes");
			expect(field).toHaveAccessibleDescription("Help");
			expect(field).toHaveAttribute("aria-invalid", "true");
			await user.click(field);
			await user.tab();
		}
		expect(blur).toHaveBeenCalledTimes(2);
	});

	it("provides keyboard navigation for the mode controls", async () => {
		const user = userEvent.setup();
		render(<TiptapEditor content="Hello" ariaLabel="Notes" />);
		await ready();
		await user.tab();
		expect(screen.getByRole("radio", { name: "Visual" })).toHaveFocus();
		await user.keyboard("{ArrowRight}");
		await waitFor(() =>
			expect(
				screen.getByRole("radio", { name: "Markdown" }),
			).toHaveFocus(),
		);
		await user.keyboard(" ");
		expect(screen.getByRole("radio", { name: "Markdown" })).toHaveAttribute(
			"aria-checked",
			"true",
		);
	});

	it("keeps initial read-only rendering free of editing controls and change events", async () => {
		const change = vi.fn();
		const { container } = render(
			<TiptapEditor
				content="## Hello"
				readOnly
				onChange={change}
				ariaLabel="Notes"
				editorClassName="min-h-0 p-0"
			/>,
		);
		await ready();
		expect(
			screen.queryByRole("radiogroup", { name: "Editor mode" }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveAttribute(
			"contenteditable",
			"false",
		);
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveClass(
			"tiptap-editor",
			"min-h-0",
		);
		expect(container.firstChild).toHaveClass(
			"rounded-[var(--bf-radius-surface)]",
			"border-border/70",
		);
		expect(change).not.toHaveBeenCalled();
	});

	it("preserves source mode through temporary read-only save states", async () => {
		const user = userEvent.setup();
		const change = vi.fn();
		const { rerender } = render(
			<TiptapEditor
				content={unsupported}
				onChange={change}
				ariaLabel="Notes"
			/>,
		);
		await ready();
		await mode(user, "Markdown");
		rerender(
			<TiptapEditor
				content={unsupported}
				readOnly
				onChange={change}
				ariaLabel="Notes"
			/>,
		);
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveAttribute(
			"readonly",
		);
		expect(
			screen.queryByRole("radiogroup", { name: "Editor mode" }),
		).not.toBeInTheDocument();
		rerender(
			<TiptapEditor
				content={unsupported}
				onChange={change}
				ariaLabel="Notes"
			/>,
		);
		expect(
			screen.getByRole("textbox", { name: "Notes" }),
		).not.toHaveAttribute("readonly");
		expect(screen.getByRole("textbox", { name: "Notes" })).toHaveValue(
			unsupported,
		);
		expect(change).not.toHaveBeenCalled();
	});
});
