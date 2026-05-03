/**
 * Component tests for PolicyEditor (Task 1 shell).
 *
 * The editor is now a tabbed shell — the Form/JSON/YAML tab content is
 * stubbed in this task and gets filled in by Tasks 2 and 3. These tests
 * cover the responsibilities the shell already owns:
 *   - empty-state hint + "Add policy"
 *   - template insertion via the toolbar Select
 *   - tab switching with placeholder content
 *   - Reference button opens the side sheet
 *
 * Task 6 owns the full rewrite once the real tab content lands.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
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
});

describe("PolicyEditor — tab shell", () => {
	it("renders the Form tab by default with the placeholder stub when policies exist", () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		renderWithProviders(<PolicyEditor value={value} onChange={onChange} />);
		expect(screen.getByTestId("form-tab-stub")).toBeInTheDocument();
		expect(screen.getByTestId("form-tab-stub")).toHaveAttribute(
			"data-policy-count",
			"1",
		);
	});

	it("clicking the JSON tab shows the json stub", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /json/i }));
		expect(screen.getByTestId("json-tab-stub")).toBeVisible();
	});

	it("clicking the YAML tab shows the yaml stub", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyEditor value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("tab", { name: /yaml/i }));
		expect(screen.getByTestId("yaml-tab-stub")).toBeVisible();
	});

	it("Form tab still shows the empty-state hint when value is null", () => {
		renderWithProviders(<PolicyEditor value={null} onChange={onChange} />);
		expect(screen.queryByTestId("form-tab-stub")).toBeNull();
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
