import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";

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

// The shared Combobox uses Radix Popover + cmdk, which is noisy in jsdom.
// This mock keeps the public contract we care about here: searchable labels,
// descriptions, empty text, and exact value selection.
vi.mock("@/components/ui/combobox", async () => {
	const React = await import("react");

	function Combobox({
		value,
		onValueChange,
		options,
		placeholder,
		searchPlaceholder,
		emptyText,
		disabled,
		"aria-label": ariaLabel,
	}: {
		value?: string;
		onValueChange?: (v: string) => void;
		options: { value: string; label: string; description?: string }[];
		placeholder?: string;
		searchPlaceholder?: string;
		emptyText?: string;
		disabled?: boolean;
		"aria-label"?: string;
	}) {
		const [query, setQuery] = React.useState("");
		const [open, setOpen] = React.useState(false);
		const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
		const visible = options.filter((option) => {
			const searchable = [
				option.value,
				option.label,
				option.description ?? "",
			]
				.join(" ")
				.toLowerCase();
			return terms.every((term) => searchable.includes(term));
		});
		return (
			<div>
				<button
					type="button"
					aria-label={ariaLabel}
					role="combobox"
					disabled={disabled}
					aria-expanded={open}
					onClick={() => setOpen((current) => !current)}
				>
					{options.find((option) => option.value === value)?.label ??
						placeholder}
				</button>
				{open && (
					<>
						<input
							aria-label={searchPlaceholder}
							value={query}
							onChange={(event) => setQuery(event.target.value)}
						/>
						<ul aria-label={`${ariaLabel} options`}>
							{visible.length === 0 ? (
								<li>{emptyText}</li>
							) : (
								visible.map((option) => (
									<li key={option.value}>
										<button
											type="button"
											onClick={() => {
												onValueChange?.(option.value);
												setOpen(false);
											}}
										>
											<span>{option.label}</span>
											{option.description && (
												<span>
													{option.description}
												</span>
											)}
										</button>
									</li>
								))
							)}
						</ul>
					</>
				)}
			</div>
		);
	}
	return { Combobox };
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

const WITH_RULES: FilePolicy = {
	...BASE,
	policies: {
		policies: [
			{
				name: "own_files",
				description: "Owners manage their own uploads.",
				actions: ["read", "write", "delete"],
				when: { eq: [{ file: "created_by" }, { user: "user_id" }] },
				custom_future_field: { preserve: true },
			} as never,
			{ $ref: "admin_bypass" },
		],
	},
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
		expect(
			screen.getByRole("button", { name: /save policy/i }),
		).toBeDisabled();
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
				value: "policies:\n  - name: everyone_read\n    actions: [read, list]\n    when: null\n",
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

	it("shows compact rule summaries by default and keeps code editing behind Advanced", async () => {
		mockListRules.mockResolvedValue([
			{
				...RULE,
				description: "Platform Admin Access can do anything.",
				body: {
					actions: ["read", "write", "delete", "list"],
					when: { authority: "platform_admin" },
				},
			},
		]);
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={WITH_RULES}
				onSave={vi.fn()}
				onDelete={vi.fn()}
				compact
			/>,
		);
		expect(
			screen.getByRole("list", { name: "Policy rules" }),
		).toBeInTheDocument();
		expect(screen.getByText("Own Files")).toBeInTheDocument();
		expect(screen.getByText("Platform Admin Access")).toBeInTheDocument();
		expect(
			await screen.findByText("Requires Platform Admin Access."),
		).toBeInTheDocument();
		expect(
			screen.queryByLabelText("file-policies.yaml"),
		).not.toBeInTheDocument();
		fireEvent.click(screen.getByRole("switch", { name: /^advanced$/i }));
		expect(
			(
				(await screen.findByLabelText(
					"file-policies.yaml",
				)) as HTMLTextAreaElement
			).value,
		).toContain("custom_future_field");
	});

	it("removes compact rules by index without rewriting the remaining document", async () => {
		mockListRules.mockResolvedValue([RULE]);
		const onSave = vi.fn();
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={WITH_RULES}
				onSave={onSave}
				onDelete={vi.fn()}
				compact
			/>,
		);
		fireEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		const saved = onSave.mock.calls[0][0];
		expect(saved.policies.policies).toEqual([{ $ref: "admin_bypass" }]);
	});

	it("does not summarize unresolved shared references as a predicate decision", () => {
		mockListRules.mockResolvedValue([]);
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={{
					...BASE,
					policies: { policies: [{ $ref: "missing_rule" }] },
				}}
				onSave={vi.fn()}
				onDelete={vi.fn()}
				compact
			/>,
		);
		expect(
			screen.getByText("Shared rule details are unavailable."),
		).toBeInTheDocument();
		expect(
			screen.queryByText("Applies to everyone."),
		).not.toBeInTheDocument();
	});
});

describe("FilePolicyEditor", () => {
	it("surfaces generic save failures inline and allows retry", async () => {
		mockListRules.mockResolvedValue([]);
		const onSave = vi
			.fn()
			.mockRejectedValueOnce(new Error("backend exploded"))
			.mockResolvedValueOnce(undefined);
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
			expect(screen.getByRole("alert")).toHaveTextContent(
				/backend exploded/i,
			),
		);
		fireEvent.click(screen.getByRole("button", { name: /retry save/i }));
		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
	});

	it("surfaces generic delete failures inline and allows retry", async () => {
		mockListRules.mockResolvedValue([]);
		const onDelete = vi
			.fn()
			.mockRejectedValueOnce(new Error("delete failed"))
			.mockResolvedValueOnce(undefined);
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={onDelete}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: /delete/i }));
		await waitFor(() =>
			expect(screen.getByRole("alert")).toHaveTextContent(
				/delete failed/i,
			),
		);
		fireEvent.click(screen.getByRole("button", { name: /retry delete/i }));
		await waitFor(() => expect(onDelete).toHaveBeenCalledTimes(2));
	});

	it("notifies busy state while a save is in flight", async () => {
		mockListRules.mockResolvedValue([]);
		let resolveSave!: () => void;
		const onBusyChange = vi.fn();
		const onSave = vi.fn(
			() =>
				new Promise<void>((resolve) => {
					resolveSave = resolve;
				}),
		);
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
				onBusyChange={onBusyChange}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		expect(onBusyChange).toHaveBeenCalledWith(true);
		resolveSave();
		await waitFor(() =>
			expect(onBusyChange).toHaveBeenLastCalledWith(false),
		);
	});
});

describe("FilePolicyEditor — reference mode", () => {
	it("documents Platform Admin Access separately from the Platform Admin role", () => {
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.click(
			screen.getByRole("button", { name: /file policy reference/i }),
		);
		expect(
			screen.getByText(
				"Boolean. True when the user holds the Platform Admin role.",
			),
		).toBeInTheDocument();
		expect(screen.getByText("Platform Admin Access")).toBeInTheDocument();
		expect(screen.getByText(/^Expression.*Full workflow executions/)).toBeInTheDocument();
	});

	it("does not render Add Shared Rule when no rules are available", () => {
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
			screen.queryByLabelText(/add shared rule/i),
		).not.toBeInTheDocument();
	});

	it("renders Add Shared Rule dropdown when rules are returned", async () => {
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
			expect(
				screen.getByLabelText(/add shared rule/i),
			).toBeInTheDocument(),
		);
	});

	it("filters template options by human labels and descriptions", () => {
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.click(screen.getByLabelText(/add template/i));
		expect(screen.getByText("Own Files")).toBeInTheDocument();
		expect(
			screen.getByText("The uploader can read/write/delete their own files."),
		).toBeInTheDocument();
		fireEvent.change(screen.getByLabelText("Search templates..."), {
			target: { value: "specific role" },
		});
		expect(screen.getByText("Role Gated Read")).toBeInTheDocument();
		expect(screen.queryByText("Own Files")).not.toBeInTheDocument();
	});

	it("filters shared-rule options by human labels and descriptions", async () => {
		mockListRules.mockResolvedValue([
			{
				...RULE,
				name: "support_triage",
				description: "Help desk can inspect uploaded evidence.",
			},
			{
				...RULE,
				name: "finance_download",
				description: "Finance team can list monthly exports.",
			},
		]);
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.click(await screen.findByLabelText(/add shared rule/i));
		await screen.findByText("Support Triage");
		fireEvent.change(screen.getByLabelText("Search shared rules..."), {
			target: { value: "monthly exports" },
		});
		expect(screen.getByText("Finance Download")).toBeInTheDocument();
		expect(screen.queryByText("Support Triage")).not.toBeInTheDocument();
	});

	it("refreshes shared rules when the picker is focused and when rulesRefreshKey changes", async () => {
		mockListRules
			.mockResolvedValueOnce([])
			.mockResolvedValueOnce([
				{
					...RULE,
					name: "support_triage",
					description: "Help desk can inspect uploaded evidence.",
				},
			])
			.mockResolvedValueOnce([
				{
					...RULE,
					name: "auditor_read",
					description: "Auditors can inspect files.",
				},
			])
			.mockResolvedValue([
				{
					...RULE,
					name: "auditor_read",
					description: "Auditors can inspect files.",
				},
			]);
		const { rerender } = renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
				compact
			/>,
		);
		const picker = screen.getByLabelText(/add shared rule/i);
		fireEvent.focus(picker);
		fireEvent.click(picker);
		await waitFor(() =>
			expect(screen.getByText("Support Triage")).toBeInTheDocument(),
		);
		rerender(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
				compact
				rulesRefreshKey={1}
			/>,
		);
		await waitFor(() =>
			expect(mockListRules).toHaveBeenCalledTimes(3),
		);
	});

	it("offers a retry when rule loading fails", async () => {
		mockListRules
			.mockRejectedValueOnce(new Error("network down"))
			.mockResolvedValueOnce([RULE]);
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
			/>,
		);
		await waitFor(() =>
			expect(
				screen.getByText(/unable to load file policy rules/i),
			).toBeInTheDocument(),
		);
		fireEvent.click(
			screen.getByRole("button", { name: /retry loading rules/i }),
		);
		await waitFor(() =>
			expect(
				screen.getByLabelText(/add shared rule/i),
			).toBeInTheDocument(),
		);
	});

	it("inserts a {$ref} entry into the policy doc when a rule is picked", async () => {
		mockListRules.mockResolvedValue([{ ...RULE, name: "shared_admin" }]);
		const onSave = vi.fn();
		renderWithProviders(
			<FilePolicyEditor
				path="reports/"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.click(await screen.findByLabelText(/add shared rule/i));
		fireEvent.click(
			screen.getByRole("button", { name: /shared admin/i }),
		);
		// Save and check the inserted entry.
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		expect(onSave).toHaveBeenCalledTimes(1);
		const saved = onSave.mock.calls[0][0];
		// The inserted entry preserves the exact shared-rule ref value.
		expect(saved.policies.policies).toHaveLength(1);
		expect(saved.policies.policies[0]).toEqual({ $ref: "shared_admin" });
	});

	it("surfaces structured 422 save errors inline", async () => {
		mockListRules.mockResolvedValue([]);
		const saveErrors = {
			errors: [
				{
					path: "$.policies[0].$ref",
					message: "unresolvable ref: missing_rule",
				},
			],
		};
		// onSave throws an error whose message is the serialized detail JSON.
		const onSave = vi
			.fn()
			.mockRejectedValue(new Error(JSON.stringify(saveErrors)));
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
			expect(
				screen.getByTestId("file-policy-save-errors"),
			).toBeInTheDocument(),
		);
		expect(
			screen.getByText(/unresolvable ref: missing_rule/i),
		).toBeInTheDocument();
	});

	it("clears save errors when the editor content changes", async () => {
		mockListRules.mockResolvedValue([]);
		const saveErrors = {
			errors: [
				{ path: "$.policies[0].$ref", message: "unresolvable ref" },
			],
		};
		const onSave = vi
			.fn()
			.mockRejectedValue(new Error(JSON.stringify(saveErrors)));
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
			expect(
				screen.getByTestId("file-policy-save-errors"),
			).toBeInTheDocument(),
		);
		// Edit the doc with a new policy — this changes the parsed value so
		// onChange fires and save errors are cleared.
		fireEvent.change(screen.getByLabelText("file-policies.yaml"), {
			target: {
				value: "policies:\n  - name: everyone_read\n    actions: [read]\n    when: null\n",
			},
		});
		await waitFor(() =>
			expect(
				screen.queryByTestId("file-policy-save-errors"),
			).not.toBeInTheDocument(),
		);
	});
});
