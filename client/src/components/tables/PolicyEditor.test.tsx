/**
 * Component tests for PolicyEditor.
 *
 * The editor is a three-tab shell (Form / JSON / YAML). The JSON and YAML
 * tabs each render a single Monaco editor for the whole `TablePolicies`
 * document; this test file mocks `@monaco-editor/react` to a textarea
 * labelled by its `path` prop so we can drive the buffers from tests.
 *
 * Coverage here:
 *   - empty-state hint + "Add policy"
 *   - template insertion via the toolbar Select
 *   - tab switching renders the right editor
 *   - JSON tab shows pretty-printed JSON of `value`
 *   - JSON / YAML keystrokes parse and emit
 *   - clearing a code tab collapses to null
 *   - invalid JSON surfaces the parse-error row
 *   - tab switch is blocked while a code tab has an unresolved parse error
 *   - inserting a template from the JSON tab reseeds the JSON buffer
 *   - Reference button opens the side sheet
 *
 * Task 6 owns the full integration rewrite that crosses tab boundaries.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
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

import { PolicyEditor } from "./PolicyEditor";
import type { components } from "@/lib/v1";

type TablePolicies = components["schemas"]["TablePolicies"];

let onChange: ReturnType<
	typeof vi.fn<(next: TablePolicies | null) => void>
>;

beforeEach(() => {
	onChange = vi.fn<(next: TablePolicies | null) => void>();
});

function lastEmitted(): TablePolicies | null {
	return onChange.mock.calls.at(-1)?.[0] as TablePolicies | null;
}

describe("PolicyEditor — empty state", () => {
	it("renders the empty-state hint when value is null", () => {
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		expect(screen.getByText(/no policies/i)).toBeInTheDocument();
		expect(screen.getByText(/use a template or click "add policy"/i))
			.toBeInTheDocument();
	});

	it("Add policy emits a single-policy TablePolicies object", async () => {
		const { user } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		await user.click(screen.getByRole("button", { name: /add policy/i }));
		const emitted = lastEmitted();
		expect(emitted).not.toBeNull();
		expect(emitted!.policies).toHaveLength(1);
		expect(emitted!.policies![0]!.name).toBe("new_policy");
		expect(emitted!.policies![0]!.actions).toEqual(["read"]);
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

	it("inserting a template while the JSON tab is active reseeds the JSON buffer", async () => {
		// Regression: AST mutations driven from outside the active code tab
		// (template insert / Add policy / Remove policy) used to leave the
		// active tab's buffer stale because emit() always skipped the active
		// tab. The user would see "no change" until they tab away and back.
		// The fix is the `resyncBuffers` opt-in on emit(); this test pins it.
		const { user, rerender } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /json/i }));
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
	it("shows pretty-printed JSON of value when value is non-null", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /json/i }));
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		expect(editor.value).toBe(JSON.stringify(value, null, 2));
	});

	it("typing valid JSON emits parsed TablePolicies on every keystroke", async () => {
		const { user } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /json/i }));
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

	it("clearing the JSON buffer collapses to null", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /json/i }));
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		fireEvent.change(editor, { target: { value: "" } });
		expect(lastEmitted()).toBeNull();
	});

	it("invalid JSON shows the parse-error row and does not emit", async () => {
		const { user } = renderWithProviders(
			<PolicyEditor value={null} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /json/i }));
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
		await user.click(screen.getByRole("tab", { name: /json/i }));
		const editor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		fireEvent.change(editor, { target: { value: "{not json" } });
		// Try to switch back to Form. The parse-error row stays, and the
		// JSON editor remains visible (i.e. activeTab did not change).
		await user.click(screen.getByRole("tab", { name: /^form$/i }));
		expect(
			screen.getByTestId("policy-editor-parse-error"),
		).toBeInTheDocument();
		expect(screen.getByLabelText("policies.json")).toBeVisible();
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
});

describe("PolicyEditor — tab shell", () => {
	it("renders the Form tab by default with the live PolicyFormView when policies exist", () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		renderWithProviders(<PolicyEditor value={value} onChange={onChange} />);
		// The Form view renders one policy row per policy in `value`.
		expect(screen.getAllByTestId(/^policy-row-/).length).toBe(1);
	});

	it("clicking the JSON tab shows the JSON Monaco editor", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /json/i }));
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

	it("Form tab still shows the empty-state hint when value is null", () => {
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		expect(screen.queryAllByTestId(/^policy-row-/).length).toBe(0);
		expect(screen.getByText(/no policies/i)).toBeInTheDocument();
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

