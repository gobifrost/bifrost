/**
 * Component tests for PolicyFormView (Form tab content).
 *
 * Focused unit-level coverage — Task 6 owns the broader PolicyEditor rewrite.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
	renderWithProviders,
	screen,
	within,
	fireEvent,
} from "@/test-utils";
import type { ReactNode } from "react";

// Radix Select uses pointer events that jsdom doesn't fully implement; swap
// for a native <select> wired through a context so SelectItem children can
// register their values into the parent's <option> list. Same shape as the
// other policy-editor tests.
vi.mock("@/components/ui/select", async () => {
	const React = await import("react");
	type Item = { value: string; label: string };
	const Ctx = React.createContext<{
		register: (it: Item) => void;
		ariaLabel?: string;
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
		const [ariaLabel, setAriaLabel] = React.useState<string | undefined>();
		const register = React.useCallback((it: Item) => {
			setItems((prev) =>
				prev.some((p) => p.value === it.value) ? prev : [...prev, it],
			);
		}, []);
		// SelectTrigger walks children to find its aria-label and pushes it up
		// via context. We mimic this via a side-channel ref in the trigger.
		const ctxValue = React.useMemo(
			() => ({ register, ariaLabel, setAriaLabel }),
			[register, ariaLabel],
		);
		return (
			<Ctx.Provider
				value={ctxValue as unknown as { register: (it: Item) => void }}
			>
				<select
					aria-label={ariaLabel}
					value={value ?? ""}
					onChange={(e) => onValueChange?.(e.target.value)}
				>
					{items.map((it) => (
						<option key={it.value} value={it.value}>
							{it.label}
						</option>
					))}
				</select>
				<div style={{ display: "none" }}>{children}</div>
			</Ctx.Provider>
		);
	}
	const Pass = ({ children }: { children: ReactNode }) => <>{children}</>;
	function SelectTrigger({
		children,
		"aria-label": ariaLabel,
	}: {
		children: ReactNode;
		"aria-label"?: string;
	}) {
		const ctx = React.useContext(Ctx) as unknown as
			| {
					setAriaLabel?: (v: string | undefined) => void;
			  }
			| null;
		React.useEffect(() => {
			if (ctx && ctx.setAriaLabel && ariaLabel)
				ctx.setAriaLabel(ariaLabel);
		}, [ctx, ariaLabel]);
		return <>{children}</>;
	}
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
		SelectTrigger,
		SelectValue: () => null,
	};
});

import { PolicyFormView } from "./PolicyFormView";
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

describe("PolicyFormView — empty state", () => {
	it("renders the empty hint when value is null", () => {
		renderWithProviders(<PolicyFormView value={null} onChange={onChange} />);
		expect(screen.getByText(/no policies/i)).toBeInTheDocument();
	});

	it("Add policy from empty state appends a default policy", async () => {
		const { user } = renderWithProviders(
			<PolicyFormView value={null} onChange={onChange} />,
		);
		await user.click(screen.getByRole("button", { name: /add policy/i }));
		const emitted = lastEmitted();
		expect(emitted!.policies).toHaveLength(1);
		expect(emitted!.policies![0]!.name).toBe("new_policy");
		expect(emitted!.policies![0]!.actions).toEqual(["read"]);
	});
});

describe("PolicyFormView — list rendering", () => {
	it("renders one row per policy, both collapsed by default", () => {
		const value: TablePolicies = {
			policies: [
				{ name: "p1", actions: ["read"], when: null },
				{
					name: "p2",
					actions: ["read", "update"],
					when: { user: "is_platform_admin" },
				},
			],
		};
		renderWithProviders(<PolicyFormView value={value} onChange={onChange} />);
		expect(screen.getAllByTestId(/^policy-row-/)).toHaveLength(2);
		// Collapsed → no expanded panel.
		expect(
			screen.queryAllByTestId(/^policy-row-expanded-/),
		).toHaveLength(0);
	});

	it("clicking the chevron expands and re-collapses the row", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyFormView value={value} onChange={onChange} />,
		);
		const row = screen.getByTestId(/^policy-row-/);
		const chevron = within(row).getByRole("button", { name: /expand/i });
		await user.click(chevron);
		expect(
			screen.getByTestId(/^policy-row-expanded-/),
		).toBeInTheDocument();
		await user.click(within(row).getByRole("button", { name: /collapse/i }));
		expect(
			screen.queryByTestId(/^policy-row-expanded-/),
		).not.toBeInTheDocument();
	});
});

describe("PolicyFormView — mutations", () => {
	it("editing the name emits an updated policy", () => {
		// Component is fully controlled; the parent doesn't update value
		// between keystrokes, so use fireEvent.change for the full final
		// string to assert the round-trip in one shot.
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		renderWithProviders(
			<PolicyFormView value={value} onChange={onChange} />,
		);
		const nameInput = screen.getByDisplayValue("p1");
		fireEvent.change(nameInput, { target: { value: "renamed" } });
		const last = lastEmitted();
		expect(last!.policies![0]!.name).toBe("renamed");
	});

	it("editing the description emits the new value", async () => {
		const value: TablePolicies = {
			policies: [
				{
					name: "p1",
					description: "old",
					actions: ["read"],
					when: null,
				},
			],
		};
		const { user } = renderWithProviders(
			<PolicyFormView value={value} onChange={onChange} />,
		);
		// Expand first.
		await user.click(screen.getByRole("button", { name: /expand/i }));
		const descInput = screen.getByDisplayValue("old");
		await user.clear(descInput);
		const last = lastEmitted();
		expect(last!.policies![0]!.description).toBeNull();
	});

	it("toggling an action checkbox adds it", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyFormView value={value} onChange={onChange} />,
		);
		await user.click(screen.getByLabelText(/^create$/i));
		const last = lastEmitted();
		expect(last!.policies![0]!.actions).toEqual(
			expect.arrayContaining(["read", "create"]),
		);
	});

	it("toggling a checked action removes it", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read", "update"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyFormView value={value} onChange={onChange} />,
		);
		await user.click(screen.getByLabelText(/^update$/i));
		const last = lastEmitted();
		expect(last!.policies![0]!.actions).toEqual(["read"]);
	});

	it("trash on the last row emits onChange(null)", async () => {
		const value: TablePolicies = {
			policies: [{ name: "p1", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyFormView value={value} onChange={onChange} />,
		);
		await user.click(
			screen.getByRole("button", { name: /remove policy/i }),
		);
		expect(lastEmitted()).toBeNull();
	});

	it("trash on one of N rows drops only that row", async () => {
		const value: TablePolicies = {
			policies: [
				{ name: "keep", actions: ["read"], when: null },
				{ name: "drop", actions: ["read"], when: null },
			],
		};
		const { user } = renderWithProviders(
			<PolicyFormView value={value} onChange={onChange} />,
		);
		await user.click(
			screen.getByRole("button", { name: /remove policy drop/i }),
		);
		const last = lastEmitted();
		expect(last!.policies).toHaveLength(1);
		expect(last!.policies![0]!.name).toBe("keep");
	});

	it("Add policy in non-empty state appends the default", async () => {
		const value: TablePolicies = {
			policies: [{ name: "existing", actions: ["read"], when: null }],
		};
		const { user } = renderWithProviders(
			<PolicyFormView value={value} onChange={onChange} />,
		);
		await user.click(screen.getByRole("button", { name: /add policy/i }));
		const last = lastEmitted();
		expect(last!.policies).toHaveLength(2);
		expect(last!.policies![1]!.name).toBe("new_policy");
	});
});
