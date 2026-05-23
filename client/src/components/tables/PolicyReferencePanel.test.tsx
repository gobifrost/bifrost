/**
 * Component tests for PolicyReferencePanel.
 *
 * The panel is now a self-contained wrapper around the shared `<HelpSlideout>`:
 * it owns the help-icon trigger and the right-side sheet that documents the
 * policy AST. Each test renders the component and clicks the trigger (labelled
 * "Policy reference") to open the sheet before asserting body content.
 *
 * Coverage:
 *   - All four legacy reference sections render when open
 *   - Worked examples block renders >= 16 patterns and includes the
 *     canonical names (admin_bypass, manager_reads_reports, ...)
 *   - Each example row exposes a Copy button and clicking it flips the
 *     button text to "Copied!" then back. We don't assert clipboard write —
 *     jsdom omits navigator.clipboard and the spec calls that out.
 *   - Footguns section is present with at least 5 entries.
 *
 * Examples are rendered through `CodeEditor` (the Monaco wrapper) so mock
 * `@monaco-editor/react` to a textarea labelled by its `path` prop — matching
 * the pattern in PolicyEditor.test.tsx / TableDialog.test.tsx.
 */

import { describe, it, expect, vi } from "vitest";
import {
	renderWithProviders,
	screen,
	within,
	waitFor,
	fireEvent,
	act,
} from "@/test-utils";

vi.mock("@monaco-editor/react", () => ({
	default: ({
		value,
		onChange,
		path,
	}: {
		value?: string;
		onChange?: (v: string | undefined) => void;
		path?: string;
	}) => (
		<textarea
			aria-label={path ?? "monaco-editor"}
			value={value ?? ""}
			onChange={(e) => onChange?.(e.target.value)}
		/>
	),
}));

vi.mock("@/contexts/ThemeContext", () => ({
	useTheme: () => ({ theme: "light" }),
}));

import { PolicyReferencePanel } from "./PolicyReferencePanel";

/**
 * Render the panel and open its sheet by clicking the help-icon trigger.
 * Returns the testing-library result for the caller.
 */
function renderAndOpen() {
	const result = renderWithProviders(<PolicyReferencePanel />);
	const triggers = screen.getAllByRole("button", {
		name: /policy reference/i,
	});
	// The trigger is the first button labelled "Policy reference"; the sheet's
	// SheetTitle is also accessible by that name once open, but the trigger is
	// always rendered first in the DOM.
	fireEvent.click(triggers[0]!);
	return result;
}

describe("PolicyReferencePanel — legacy sections", () => {
	it("renders USER fields, ROW fields, Functions, and Operators when open", () => {
		renderAndOpen();
		expect(
			screen.getByRole("heading", { name: /USER fields/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: /ROW fields/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: /Functions/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: /Operators/i }),
		).toBeInTheDocument();
	});

	it("does not render body content until the trigger is clicked", () => {
		renderWithProviders(<PolicyReferencePanel />);
		expect(
			screen.queryByRole("heading", { name: /USER fields/i }),
		).not.toBeInTheDocument();
	});
});

describe("PolicyReferencePanel — worked examples", () => {
	it("renders at least 16 example headings", () => {
		renderAndOpen();
		const exampleHeadings = screen.getAllByRole("heading", { level: 5 });
		expect(exampleHeadings.length).toBeGreaterThanOrEqual(16);
	});

	it("includes the canonical example names", () => {
		renderAndOpen();
		expect(
			screen.getByRole("heading", { level: 5, name: "admin_bypass" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", {
				level: 5,
				name: "manager_reads_reports",
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { level: 5, name: "own_row" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", {
				level: 5,
				name: "provider_read",
			}),
		).toBeInTheDocument();
	});

	it("renders a Copy button for each example", () => {
		renderAndOpen();
		const exampleHeadings = screen.getAllByRole("heading", { level: 5 });
		const copyButtons = screen.getAllByRole("button", { name: /^copy$/i });
		expect(copyButtons.length).toBe(exampleHeadings.length);
	});

	it("wraps each example JSON with the {policies: [...]} document so paste-into-fresh-JSON works", () => {
		// Each pretty-printed example must be a parseable TablePolicies
		// document (i.e. starts with the wrapper) — the plan calls this out
		// explicitly so users can copy → paste into the JSON tab without
		// hand-editing the wrapper.
		//
		// Examples render through CodeEditor (mocked to a textarea labelled
		// `example-<idx>.json`); pull each textarea by its label and parse.
		renderAndOpen();
		const headings = screen.getAllByRole("heading", { level: 5 });
		const editors = screen.getAllByLabelText(/^example-\d+\.json$/);
		expect(editors.length).toBe(headings.length);
		for (let i = 0; i < headings.length; i++) {
			const heading = headings[i]!;
			const editor = screen.getByLabelText(
				`example-${i}.json`,
			) as HTMLTextAreaElement;
			const parsed = JSON.parse(editor.value);
			expect(parsed).toHaveProperty("policies");
			expect(Array.isArray(parsed.policies)).toBe(true);
			expect(parsed.policies.length).toBeGreaterThan(0);
			// Heading matches the inner policy's name.
			expect(parsed.policies[0].name).toBe(heading.textContent);
		}
	});

	it("flips Copy button to Copied! on click and resets", async () => {
		// Stub clipboard so the click handler doesn't throw in jsdom. We don't
		// assert the call payload — only the visible state transition.
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, "clipboard", {
			configurable: true,
			value: { writeText },
		});

		// Radix's Sheet portal + jsdom pointer-events make userEvent.click
		// flaky here. fireEvent.click is sufficient for asserting the state
		// transition we care about. We use fake timers ONLY around the
		// 1500ms reset window so React Testing Library's async helpers
		// (findBy*, waitFor) keep working with real timers everywhere else.
		renderAndOpen();
		const firstCopy = screen.getAllByRole("button", {
			name: /^copy$/i,
		})[0]!;

		vi.useFakeTimers({ shouldAdvanceTime: true });
		try {
			fireEvent.click(firstCopy);
			expect(
				screen.getAllByRole("button", { name: /copied!/i }).length,
			).toBeGreaterThanOrEqual(1);
			act(() => {
				vi.advanceTimersByTime(2000);
			});
			await waitFor(() =>
				expect(
					screen.queryByRole("button", { name: /copied!/i }),
				).not.toBeInTheDocument(),
			);
		} finally {
			vi.useRealTimers();
		}
	});
});

describe("PolicyReferencePanel — footguns", () => {
	it("renders the Footguns section with at least 5 entries", () => {
		renderAndOpen();
		const heading = screen.getByRole("heading", { name: /footguns/i });
		expect(heading).toBeInTheDocument();
		// The Footguns dl is the heading's next sibling; count <dt> entries.
		const section = heading.closest("section");
		expect(section).not.toBeNull();
		const titles = within(section!).getAllByRole("term");
		expect(titles.length).toBeGreaterThanOrEqual(5);
	});

	it("calls out the null-in-eq and not+is_null gotchas", () => {
		renderAndOpen();
		const heading = screen.getByRole("heading", { name: /footguns/i });
		const section = heading.closest("section")!;
		expect(
			within(section).getByText(/null in eq is invalid/i),
		).toBeInTheDocument();
		expect(
			within(section).getByText(/is set.*idiom/i),
		).toBeInTheDocument();
	});
});
