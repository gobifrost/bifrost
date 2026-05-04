/**
 * Component tests for PolicyEditor.
 *
 * The editor is a two-tab shell (JSON / YAML). Each tab renders a single
 * Monaco editor for the whole `TablePolicies` document; this test file
 * mocks `@monaco-editor/react` to a textarea labelled by its `path` prop
 * so we can drive the buffers from tests.
 *
 * Coverage here:
 *   - empty-buffer seeding: value=null seeds the JSON/YAML buffers to the
 *     `{policies: []}` wrapper so users have a wrapper to paste into
 *   - template insertion via the toolbar Select
 *   - tab switching renders the right editor
 *   - JSON tab shows pretty-printed JSON of `value`
 *   - JSON / YAML keystrokes parse and emit
 *   - clearing a code tab collapses to null
 *   - invalid JSON surfaces the parse-error row
 *   - tab switch is blocked while a code tab has an unresolved parse error
 *   - inserting a template from the JSON tab reseeds the JSON buffer
 *   - pasting a wrapped reference example into a fresh JSON buffer parses
 *     into the expected TablePolicies
 *   - JSON ↔ YAML round-trip
 *   - Reference button opens the side sheet
 *   - server-side validation: debounced call after parse-success, error
 *     rendering, parse-error wipes stale validation results, and the
 *     null-buffer case skips the round trip entirely
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";
import type { ReactNode } from "react";

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

// Radix Select uses pointer events that jsdom doesn't fully implement; swap
// for a native <select> wired through a context so SelectItem children can
// register their values into the parent's <option> list.
vi.mock("@/components/ui/select", async () => {
	const React = await import("react");
	type Item = { value: string; label: string };
	const Ctx = React.createContext<{
		register: (it: Item) => void;
	} | null>(null);

	function Select({
		value,
		onValueChange,
		children,
	}: {
		value?: string;
		onValueChange?: (v: string) => void;
		children: ReactNode;
	}) {
		const [items, setItems] = React.useState<Item[]>([]);
		const register = React.useCallback((it: Item) => {
			setItems((prev) =>
				prev.some((p) => p.value === it.value) ? prev : [...prev, it],
			);
		}, []);
		return (
			<Ctx.Provider value={{ register }}>
				<select
					aria-label="Insert template"
					value={value ?? ""}
					onChange={(e) => onValueChange?.(e.target.value)}
				>
					<option value="">Insert template...</option>
					{items.map((it) => (
						<option key={it.value} value={it.value}>
							{it.label}
						</option>
					))}
				</select>
				{/* Children are rendered (invisibly) so SelectItem can register. */}
				<div style={{ display: "none" }}>{children}</div>
			</Ctx.Provider>
		);
	}
	const Pass = ({ children }: { children: ReactNode }) => <>{children}</>;
	function SelectItem({
		value,
		children,
	}: {
		value: string;
		children: ReactNode;
	}) {
		const ctx = React.useContext(Ctx);
		React.useEffect(() => {
			ctx?.register({ value, label: String(children) });
		}, [ctx, value, children]);
		return null;
	}
	return {
		Select,
		SelectContent: Pass,
		SelectGroup: Pass,
		SelectItem,
		SelectLabel: Pass,
		SelectScrollDownButton: () => null,
		SelectScrollUpButton: () => null,
		SelectSeparator: () => null,
		SelectTrigger: Pass,
		SelectValue: () => null,
	};
});

// Mock the policies-service entry point. Tests that need to exercise
// validation behavior import the mock factory and re-wire `validatePolicies`
// per case. The default returns `{ok: true}` so unrelated tests aren't
// perturbed when the editor's debounced validate effect fires.
vi.mock("@/services/tables", () => ({
	validatePolicies: vi.fn(async () => ({ ok: true, errors: [] })),
}));

import { useState } from "react";
import { PolicyEditor } from "./PolicyEditor";
import { validatePolicies } from "@/services/tables";
import type { components } from "@/lib/v1";

const mockValidate = validatePolicies as unknown as ReturnType<typeof vi.fn>;

type TablePolicies = components["schemas"]["TablePolicies"];

let onChange: ReturnType<
	typeof vi.fn<(next: TablePolicies | null) => void>
>;

beforeEach(() => {
	onChange = vi.fn<(next: TablePolicies | null) => void>();
	mockValidate.mockReset();
	// Default per-test impl: clean validation. Individual tests override
	// to drive the error path.
	mockValidate.mockImplementation(async () => ({ ok: true, errors: [] }));
});

function lastEmitted(): TablePolicies | null {
	return onChange.mock.calls.at(-1)?.[0] as TablePolicies | null;
}

describe("PolicyEditor — empty-buffer seeding", () => {
	it("seeds the JSON tab to {\"policies\": []} when value is null", () => {
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		expect(editor.value).toBe(JSON.stringify({ policies: [] }, null, 2));
	});

	it("seeds the YAML tab to `policies: []` when value is null", async () => {
		const { user } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		const editor = screen.getByLabelText(
			"policies.yaml",
		) as HTMLTextAreaElement;
		// js-yaml.dump({policies: []}) → "policies: []\n"
		expect(editor.value).toBe("policies: []\n");
	});

	it("clearing the JSON buffer collapses to onChange(null)", async () => {
		const { user } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		// Start from the JSON tab default (already active).
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		// Clear the seeded wrapper.
		fireEvent.change(editor, { target: { value: "" } });
		expect(lastEmitted()).toBeNull();
		// Sanity: the YAML tab also re-seeds when we hit it.
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		const yamlEditor = screen.getByLabelText(
			"policies.yaml",
		) as HTMLTextAreaElement;
		expect(yamlEditor.value).toBe("policies: []\n");
	});
});

describe("PolicyEditor — templates", () => {
	it("selecting a template inserts the template's policy", () => {
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		const select = screen.getByLabelText(
			/insert template/i,
		) as HTMLSelectElement;
		select.value = "own_row";
		select.dispatchEvent(new Event("change", { bubbles: true }));
		const emitted = lastEmitted();
		expect(emitted!.policies).toHaveLength(1);
		const inserted = emitted!.policies![0]!;
		expect(inserted.name).toBe("own_row");
		expect(inserted.actions).toEqual(["read", "update", "delete"]);
		expect(inserted.when).toEqual({
			eq: [{ row: "created_by" }, { user: "user_id" }],
		});
	});

	it("inserting a template while the JSON tab is active reseeds the JSON buffer", () => {
		// Regression: AST mutations driven from outside the active code tab
		// (template insert) used to leave the active tab's buffer stale
		// because emit() always skipped the active tab. The user would see
		// "no change" until they tabbed away and back. The fix is the
		// `resyncBuffers` opt-in on emit(); this test pins it.
		const { rerender } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		// JSON tab is active by default — no need to click.
		const select = screen.getByLabelText(
			/insert template/i,
		) as HTMLSelectElement;
		select.value = "own_row";
		select.dispatchEvent(new Event("change", { bubbles: true }));
		// Parent echoes the emitted value back via props (the real TableDialog
		// pattern). Mirror that so we exercise the same code path.
		const emitted = lastEmitted();
		rerender(<PolicyEditor value={emitted} onChange={onChange} />);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		expect(editor.value).toContain('"own_row"');
		expect(editor.value).toContain('"created_by"');
	});
});

describe("PolicyEditor — JSON tab", () => {
	it("shows pretty-printed JSON of value when value is non-null", () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		renderWithProviders(<PolicyEditor value={value} onChange={onChange} />);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		expect(editor.value).toBe(JSON.stringify(value, null, 2));
	});

	it("typing valid JSON emits parsed TablePolicies on every keystroke", () => {
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		const next = JSON.stringify(
			{ policies: [{ name: "p1", actions: ["read"], when: null }] },
			null,
			2,
		);
		fireEvent.change(editor, { target: { value: next } });
		const emitted = lastEmitted();
		expect(emitted).not.toBeNull();
		expect(emitted!.policies).toHaveLength(1);
		expect(emitted!.policies![0]!.name).toBe("p1");
	});

	it("clearing the JSON buffer collapses to null", () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		renderWithProviders(<PolicyEditor value={value} onChange={onChange} />);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		fireEvent.change(editor, { target: { value: "" } });
		expect(lastEmitted()).toBeNull();
	});

	it("invalid JSON shows the parse-error row and does not emit", () => {
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		// Sentinel so we can detect spurious emits.
		onChange.mockClear();
		fireEvent.change(editor, { target: { value: "{not json" } });
		expect(
			screen.getByTestId("policy-editor-parse-error"),
		).toBeInTheDocument();
		expect(onChange).not.toHaveBeenCalled();
	});

	it("blocks tab switch while a parse error is unresolved", async () => {
		const { user } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		fireEvent.change(editor, { target: { value: "{not json" } });
		// Try to switch to YAML. The parse-error row stays, and the JSON
		// editor remains visible (i.e. activeTab did not change).
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		expect(
			screen.getByTestId("policy-editor-parse-error"),
		).toBeInTheDocument();
		expect(screen.getByLabelText("policies.json")).toBeVisible();
	});

	it("strict parser rejects a single-Policy root (must be {policies: [...]})", () => {
		// Plan-matrix item: asTablePolicies accepts only {policies: [...]} —
		// no single-Policy fallback. Pasting a bare Policy object should
		// surface a parse error rather than silently wrapping.
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		onChange.mockClear();
		// A bare Policy object — no `policies` key at the root.
		const bare = JSON.stringify(
			{ name: "p1", actions: ["read"], when: null },
			null,
			2,
		);
		fireEvent.change(editor, { target: { value: bare } });
		expect(
			screen.getByTestId("policy-editor-parse-error"),
		).toBeInTheDocument();
		expect(onChange).not.toHaveBeenCalled();
	});

	it("pasting a wrapped reference example into the JSON tab triggers onChange with the parsed value", () => {
		// Plan-matrix item: the reference panel now exports each example as a
		// full {policies: [...]} document, so Copy → paste-into-fresh-JSON
		// produces a valid TablePolicies. Mirror one of the panel's examples
		// (own_row) and confirm the editor parses it cleanly.
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		const wrappedExample: TablePolicies = {
			policies: [
				{
					name: "own_row",
					description: "Row owner can read/update/delete",
					actions: ["read", "update", "delete"],
					when: { eq: [{ row: "created_by" }, { user: "user_id" }] },
				},
			],
		};
		fireEvent.change(editor, {
			target: { value: JSON.stringify(wrappedExample, null, 2) },
		});
		expect(lastEmitted()).toEqual(wrappedExample);
	});
});

describe("PolicyEditor — YAML tab", () => {
	it("typing valid YAML emits parsed TablePolicies", async () => {
		const { user } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		const editor = screen.getByLabelText(
			"policies.yaml",
		) as HTMLTextAreaElement;
		const yamlSrc = `policies:
  - name: p1
    actions:
      - read
    when: null
`;
		fireEvent.change(editor, { target: { value: yamlSrc } });
		const emitted = lastEmitted();
		expect(emitted).not.toBeNull();
		expect(emitted!.policies).toHaveLength(1);
		expect(emitted!.policies![0]!.name).toBe("p1");
		expect(emitted!.policies![0]!.when).toBeNull();
	});

	it("clearing the YAML buffer collapses to null", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		const editor = screen.getByLabelText(
			"policies.yaml",
		) as HTMLTextAreaElement;
		fireEvent.change(editor, { target: { value: "" } });
		expect(lastEmitted()).toBeNull();
	});

	it("serializes when:null literally so always-true rules are visible", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		const editor = screen.getByLabelText(
			"policies.yaml",
		) as HTMLTextAreaElement;
		expect(editor.value).toContain("when: null");
	});

	it("shows the YAML serialization of the current value when the tab is opened", async () => {
		// Plan-matrix item: "YAML tab (after click) shows the YAML
		// serialization." Asserts the initial buffer matches what we would
		// expect from a yaml.dump of value, not just an arbitrary substring.
		const value: TablePolicies = {
			policies: [
				{
					name: "p1",
					actions: ["read"],
					when: { eq: [{ row: "created_by" }, { user: "user_id" }] },
				},
			],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		const editor = screen.getByLabelText(
			"policies.yaml",
		) as HTMLTextAreaElement;
		// Rather than hand-rolling the expected YAML string, parse the buffer
		// back through js-yaml and assert structural equivalence with `value`.
		const yaml = await import("js-yaml");
		expect(yaml.load(editor.value, { schema: yaml.JSON_SCHEMA })).toEqual(
			value,
		);
	});
});

describe("PolicyEditor — tab shell", () => {
	it("renders the JSON tab by default", () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		renderWithProviders(<PolicyEditor value={value} onChange={onChange} />);
		expect(screen.getByLabelText("policies.json")).toBeVisible();
	});

	it("clicking the YAML tab shows the YAML Monaco editor", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		expect(screen.getByLabelText("policies.yaml")).toBeVisible();
	});

	it("does not render a Form tab", () => {
		// Regression: the v2 amendment dropped the graphical Form tab entirely.
		// If a regression resurrects it, this test pins the contract.
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		expect(
			screen.queryByRole("tab", { name: /^form$/i }),
		).not.toBeInTheDocument();
	});

	it("does not render an Add policy button", () => {
		// The Add policy button lived in the Form tab. With the Form tab
		// gone, AST mutation flows through the Insert template menu and
		// direct JSON/YAML editing.
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		expect(
			screen.queryByRole("button", { name: /add policy/i }),
		).not.toBeInTheDocument();
	});
});

describe("PolicyEditor — tab round-trip", () => {
	it("JSON ↔ YAML preserves the AST through both serializations", async () => {
		// Plan-matrix item: round-trip smoke test crossing tab boundaries.
		// Drive the editor through JSON (default) → YAML → JSON, asserting
		// the parsed contents on each tab match the structural value.
		const value: TablePolicies = {
			policies: [
				{
					name: "own_row",
					description: "owner reads + updates",
					actions: ["read", "update"],
					when: { eq: [{ row: "created_by" }, { user: "user_id" }] },
				},
			],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);

		// JSON tab (default): pretty-printed JSON parses back to the same
		// TablePolicies.
		const jsonEditor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		expect(JSON.parse(jsonEditor.value)).toEqual(value);

		// YAML tab: same AST round-trips through YAML serialization.
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		const yamlEditor = screen.getByLabelText(
			"policies.yaml",
		) as HTMLTextAreaElement;
		const yaml = await import("js-yaml");
		expect(yaml.load(yamlEditor.value, { schema: yaml.JSON_SCHEMA })).toEqual(
			value,
		);

		// Back to JSON tab — buffer is still in sync.
		await user.click(screen.getByRole("tab", { name: /json/i }));
		const jsonAgain = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		expect(JSON.parse(jsonAgain.value)).toEqual(value);
	});
});

describe("PolicyEditor — reference panel", () => {
	it("Reference button opens the side sheet", async () => {
		const { user } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		await user.click(screen.getByRole("button", { name: /reference/i }));
		expect(screen.getByText(/policy reference/i)).toBeInTheDocument();
		expect(screen.getByText(/USER fields/i)).toBeInTheDocument();
		expect(screen.getByText(/Operators/i)).toBeInTheDocument();
	});
});

// Mirror of the editor's debounce window. Kept in sync via a single named
// constant so a future tweak in the component is caught by the tests
// failing rather than silently passing on a wrong window.
const VALIDATE_DEBOUNCE_MS_TEST = 300;

/** Controlled wrapper: mirrors the real `TableDialog` parent's
 * "echo prop back on every onChange" pattern so the editor's value-driven
 * effects (in particular, the debounced validate) actually see the AST
 * the user just edited into the buffer. */
function ControlledPolicyEditor({
	initial,
	onChange: propOnChange,
}: {
	initial: TablePolicies | null;
	onChange: (next: TablePolicies | null) => void;
}) {
	const [value, setValue] = useState<TablePolicies | null>(initial);
	return (
		<PolicyEditor
			value={value}
			onChange={(next) => {
				setValue(next);
				propOnChange(next);
			}}
		/>
	);
}

describe("PolicyEditor — server-side validation", () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("calls validate exactly once after a 300ms debounce of typing", async () => {
		renderWithProviders(
			<ControlledPolicyEditor initial={null} onChange={onChange} />,
		);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		// Mount-time effect for value=null shouldn't have fired the
		// validator. Reset the call log so we don't count any spurious
		// mount-time call.
		mockValidate.mockClear();
		const valid = JSON.stringify(
			{ policies: [{ name: "p1", actions: ["read"], when: null }] },
			null,
			2,
		);
		fireEvent.change(editor, { target: { value: valid } });
		// Before the debounce expires, validate hasn't been called.
		expect(mockValidate).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(299);
		expect(mockValidate).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1);
		expect(mockValidate).toHaveBeenCalledTimes(1);
		// The call payload is the parsed AST, not the raw text.
		const arg = mockValidate.mock.calls[0]![0];
		expect(arg).toEqual({
			policies: [{ name: "p1", actions: ["read"], when: null }],
		});
	});

	it("renders validation errors returned by the server", async () => {
		mockValidate.mockResolvedValue({
			ok: false,
			errors: [
				{
					path: "$.policies[0].when.eq[1]",
					message: "eq does not accept null literals",
				},
			],
		});
		renderWithProviders(
			<ControlledPolicyEditor initial={null} onChange={onChange} />,
		);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		const valid = JSON.stringify(
			{ policies: [{ name: "p1", actions: ["read"], when: null }] },
			null,
			2,
		);
		fireEvent.change(editor, { target: { value: valid } });
		// Run the debounce + the resolved-promise microtask so the state
		// update from the validate response lands.
		await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS_TEST);
		await vi.advanceTimersByTimeAsync(0);
		// Both the path and the message should appear in the rendered row.
		const block = screen.getByTestId("policy-editor-validation-errors");
		expect(block.textContent).toContain("$.policies[0].when.eq[1]");
		expect(block.textContent).toContain("eq does not accept null literals");
	});

	it("clears validation errors when the buffer becomes invalid", async () => {
		mockValidate.mockResolvedValue({
			ok: false,
			errors: [{ path: "$.policies[0]", message: "broken" }],
		});
		renderWithProviders(
			<ControlledPolicyEditor initial={null} onChange={onChange} />,
		);
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		// Step 1: type valid JSON; let validation resolve with errors.
		const valid = JSON.stringify(
			{ policies: [{ name: "p1", actions: ["read"], when: null }] },
			null,
			2,
		);
		fireEvent.change(editor, { target: { value: valid } });
		await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS_TEST);
		await vi.advanceTimersByTimeAsync(0);
		expect(
			screen.getByTestId("policy-editor-validation-errors"),
		).toBeInTheDocument();
		// Step 2: type garbage; the validation block should disappear.
		fireEvent.change(editor, { target: { value: "{not json" } });
		expect(
			screen.queryByTestId("policy-editor-validation-errors"),
		).not.toBeInTheDocument();
		// Parse-error row takes over.
		expect(
			screen.getByTestId("policy-editor-parse-error"),
		).toBeInTheDocument();
	});

	it("does not call validate while the buffer is empty (value=null)", async () => {
		renderWithProviders(
			<ControlledPolicyEditor initial={null} onChange={onChange} />,
		);
		mockValidate.mockClear();
		// Walk past the debounce window without touching the editor; nothing
		// to validate, so no round trip.
		await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS_TEST + 50);
		expect(mockValidate).not.toHaveBeenCalled();
	});
});
