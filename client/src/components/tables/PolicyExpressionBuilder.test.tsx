/**
 * Component tests for PolicyExpressionBuilder.
 *
 * Driven through the same Radix-Select shim used by the rest of the policy
 * editor tests so jsdom can find the dropdowns by aria-label.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
	renderWithProviders,
	screen,
	fireEvent,
} from "@/test-utils";
import type { ReactNode } from "react";

vi.mock("@/components/ui/select", async () => {
	const React = await import("react");
	type Item = { value: string; label: string };
	type Ctx = {
		register: (it: Item) => void;
		setAriaLabel: (v: string | undefined) => void;
	};
	const Ctx = React.createContext<Ctx | null>(null);

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
		const ctxValue = React.useMemo(
			() => ({ register, setAriaLabel }),
			[register],
		);
		return (
			<Ctx.Provider value={ctxValue}>
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
		const ctx = React.useContext(Ctx);
		React.useEffect(() => {
			if (ctx && ariaLabel) ctx.setAriaLabel(ariaLabel);
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

import { PolicyExpressionBuilder } from "./PolicyExpressionBuilder";
import type { ExprNode } from "./expr-shapes";

let onChange: ReturnType<typeof vi.fn<(next: ExprNode | null) => void>>;
beforeEach(() => {
	onChange = vi.fn<(next: ExprNode | null) => void>();
});
function lastEmitted(): ExprNode | null | undefined {
	return onChange.mock.calls.at(-1)?.[0] as ExprNode | null | undefined;
}

describe("PolicyExpressionBuilder — always-true toggle", () => {
	it("value=null shows Always true mode active", () => {
		renderWithProviders(
			<PolicyExpressionBuilder value={null} onChange={onChange} />,
		);
		const alwaysBtn = screen.getByRole("radio", { name: /always true/i });
		expect(alwaysBtn).toHaveAttribute("aria-checked", "true");
	});

	it("toggling to Build expression emits the structurally-valid default eq node", async () => {
		const { user } = renderWithProviders(
			<PolicyExpressionBuilder value={null} onChange={onChange} />,
		);
		await user.click(
			screen.getByRole("radio", { name: /build expression/i }),
		);
		expect(lastEmitted()).toEqual({
			eq: [{ row: "" }, { user: "user_id" }],
		});
	});
});

describe("PolicyExpressionBuilder — eq with row + user refs", () => {
	it("typing a row column emits the updated AST", () => {
		// Builder is fully controlled; parent doesn't re-emit value between
		// keystrokes, so set the full string in one fireEvent.change.
		const value: ExprNode = {
			eq: [{ row: "" }, { user: "user_id" }],
		};
		renderWithProviders(
			<PolicyExpressionBuilder value={value} onChange={onChange} />,
		);
		const rowInput = screen.getByLabelText(/row field path/i);
		fireEvent.change(rowInput, { target: { value: "created_by" } });
		const last = lastEmitted();
		expect(last).toEqual({
			eq: [{ row: "created_by" }, { user: "user_id" }],
		});
	});
});

describe("PolicyExpressionBuilder — operand kind switch", () => {
	it("switching an operand kind from User-ref to Expression resets the slot to a nested default", () => {
		const value: ExprNode = {
			eq: [{ row: "" }, { user: "user_id" }],
		};
		renderWithProviders(
			<PolicyExpressionBuilder value={value} onChange={onChange} />,
		);
		// There are two operand kind pickers (one per operand). The right
		// operand is currently `user-ref`; flip it to `expression`.
		const operandPickers = screen.getAllByLabelText(/operand kind/i);
		expect(operandPickers).toHaveLength(2);
		fireEvent.change(operandPickers[1]!, {
			target: { value: "expression" },
		});
		const last = lastEmitted() as Record<string, unknown>;
		expect(last).toBeDefined();
		// New operand 1 is itself the default eq node.
		expect(last.eq).toEqual([
			{ row: "" },
			{ eq: [{ row: "" }, { user: "user_id" }] },
		]);
	});
});

describe("PolicyExpressionBuilder — in chip list", () => {
	it("typing into the chip input + Enter adds a chip and emits the literal list", () => {
		const value: ExprNode = { in: [{ row: "status" }, []] };
		renderWithProviders(
			<PolicyExpressionBuilder value={value} onChange={onChange} />,
		);
		const chipInput = screen.getByLabelText(/add list value/i);
		fireEvent.change(chipInput, { target: { value: "open" } });
		fireEvent.keyDown(chipInput, { key: "Enter" });
		const last = lastEmitted() as { in: [unknown, unknown[]] };
		expect(last.in[1]).toEqual(["open"]);
	});

	it("empty literal list shows the validator hint", () => {
		const value: ExprNode = { in: [{ row: "status" }, []] };
		renderWithProviders(
			<PolicyExpressionBuilder value={value} onChange={onChange} />,
		);
		expect(
			screen.getByText(/in: requires a non-empty list/i),
		).toBeInTheDocument();
	});
});

describe("PolicyExpressionBuilder — and operand removal", () => {
	it("removing a not-leaf operand from `and` propagates onChange", async () => {
		const value: ExprNode = {
			and: [
				{ row: "x" },
				{ row: "y" },
				{ row: "z" },
			],
		};
		const { user } = renderWithProviders(
			<PolicyExpressionBuilder value={value} onChange={onChange} />,
		);
		// Remove the second operand.
		await user.click(
			screen.getByRole("button", { name: /remove operand 2/i }),
		);
		const last = lastEmitted() as { and: unknown[] };
		expect(last.and).toEqual([{ row: "x" }, { row: "z" }]);
	});
});
