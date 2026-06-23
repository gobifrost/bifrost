import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";
import type { ReactNode } from "react";

// Monaco can't run in the test DOM — stub it to a textarea labelled by `path`.
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

// Same Select mock as PolicyEditor.test.tsx — Radix Select uses pointer events
// that jsdom doesn't fully implement. SelectTrigger forwards its aria-label.
vi.mock("@/components/ui/select", async () => {
	const React = await import("react");
	type Item = { value: string; label: string };
	const Ctx = React.createContext<{
		register: (it: Item) => void;
		setLabel: (label: string) => void;
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
		const [label, setLabel] = React.useState("Select");
		const register = React.useCallback((it: Item) => {
			setItems((prev) =>
				prev.some((p) => p.value === it.value) ? prev : [...prev, it],
			);
		}, []);
		return (
			<Ctx.Provider value={{ register, setLabel }}>
				<select
					aria-label={label}
					value={value ?? ""}
					onChange={(e) => onValueChange?.(e.target.value)}
				>
					<option value="">{label}...</option>
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
		children?: ReactNode;
		"aria-label"?: string;
		[key: string]: unknown;
	}) {
		const ctx = React.useContext(Ctx);
		React.useEffect(() => {
			if (ariaLabel) ctx?.setLabel(ariaLabel);
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

vi.mock("@/services/policyRules", () => ({
	listPolicyRules: vi.fn(async () => []),
}));

import { listPolicyRules } from "@/services/policyRules";
import { FilePolicyEditor } from "./FilePolicyEditor";

const mockListRules = listPolicyRules as unknown as ReturnType<typeof vi.fn>;

import type { FilePolicy } from "@/services/filePolicies";

const BASE: FilePolicy = {
	id: "pol-1",
	location: "workspace",
	path: "reports/",
	policies: { policies: [] },
};

const RULE = {
	id: "00000000-0000-0000-0000-000000000001",
	organization_id: null,
	name: "admin_bypass",
	domain: "file" as const,
	description: null,
	body: {},
	read_only: true,
	created_at: "2024-01-01T00:00:00Z",
	updated_at: "2024-01-01T00:00:00Z",
};

beforeEach(() => {
	mockListRules.mockReset();
	mockListRules.mockResolvedValue([]);
});

describe("FilePolicyEditor", () => {
	it("defaults to the YAML view", () => {
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
			/>,
		);
		// The YAML Monaco model (path "file-policies.yaml") is the active editor.
		expect(screen.getByLabelText("file-policies.yaml")).toBeInTheDocument();
	});

	it("blocks save while the document fails to parse", () => {
		const onSave = vi.fn();
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.change(screen.getByLabelText("file-policies.yaml"), {
			target: { value: "policies: [oops" },
		});
		expect(screen.getByText(/parse error/i)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /save policy/i })).toBeDisabled();
		expect(onSave).not.toHaveBeenCalled();
	});

	it("saves the edited document with the location/path wrapper reattached", () => {
		const onSave = vi.fn();
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.change(screen.getByLabelText("file-policies.yaml"), {
			target: {
				value:
					"policies:\n  - name: everyone_read\n    actions: [read, list]\n    when: null\n",
			},
		});
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		expect(onSave).toHaveBeenCalledTimes(1);
		const saved = onSave.mock.calls[0][0];
		expect(saved.location).toBe("workspace");
		expect(saved.path).toBe("reports/");
		expect(saved.id).toBe("pol-1");
		expect(saved.policies.policies[0].name).toBe("everyone_read");
	});
});

describe("FilePolicyEditor — reference mode", () => {
	it("does not render Insert reference when no rules are available", () => {
		mockListRules.mockResolvedValue([]);
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
			/>,
		);
		expect(
			screen.queryByLabelText(/insert reference/i),
		).not.toBeInTheDocument();
	});

	it("renders Insert reference dropdown when rules are returned", async () => {
		mockListRules.mockResolvedValue([RULE]);
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
			/>,
		);
		await waitFor(() =>
			expect(screen.getByLabelText(/insert reference/i)).toBeInTheDocument(),
		);
	});

	it("inserts a {$ref} entry into the policy doc when a rule is picked", async () => {
		mockListRules.mockResolvedValue([RULE]);
		const onSave = vi.fn();
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);
		// Wait for the dropdown to appear (rules loaded).
		const refSelect = await screen.findByLabelText(/insert reference/i);
		// Simulate picking "admin_bypass" from the select.
		fireEvent.change(refSelect, { target: { value: "admin_bypass" } });
		// Save and check the inserted entry.
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		expect(onSave).toHaveBeenCalledTimes(1);
		const saved = onSave.mock.calls[0][0];
		// The inserted entry should be { $ref: "admin_bypass" }.
		expect(saved.policies.policies).toHaveLength(1);
		expect(saved.policies.policies[0]).toEqual({ $ref: "admin_bypass" });
	});

	it("surfaces structured 422 save errors inline", async () => {
		mockListRules.mockResolvedValue([]);
		const saveErrors = {
			errors: [{ path: "$.policies[0].$ref", message: "unresolvable ref: missing_rule" }],
		};
		// onSave throws an error whose message is the serialized detail JSON.
		const onSave = vi.fn().mockRejectedValue(new Error(JSON.stringify(saveErrors)));
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		await waitFor(() =>
			expect(screen.getByTestId("file-policy-save-errors")).toBeInTheDocument(),
		);
		expect(screen.getByText(/unresolvable ref: missing_rule/i)).toBeInTheDocument();
	});

	it("clears save errors when the editor content changes", async () => {
		mockListRules.mockResolvedValue([]);
		const saveErrors = {
			errors: [{ path: "$.policies[0].$ref", message: "unresolvable ref" }],
		};
		const onSave = vi.fn().mockRejectedValue(new Error(JSON.stringify(saveErrors)));
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);
		// Trigger save errors.
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		await waitFor(() =>
			expect(screen.getByTestId("file-policy-save-errors")).toBeInTheDocument(),
		);
		// Edit the doc with a new policy — this changes the parsed value so
		// onChange fires and save errors are cleared.
		fireEvent.change(screen.getByLabelText("file-policies.yaml"), {
			target: {
				value:
					"policies:\n  - name: everyone_read\n    actions: [read]\n    when: null\n",
			},
		});
		await waitFor(() =>
			expect(
				screen.queryByTestId("file-policy-save-errors"),
			).not.toBeInTheDocument(),
		);
	});
});
