import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUpdateWorkflow = vi.fn();
const mockAssignRoles = vi.fn();
const mockRemoveRole = vi.fn();
const mockWorkflowRolesRefetch = vi.fn();
const mockUseWorkflowKeys = vi.fn();
const mockCreateKey = vi.fn();
const mockRevokeKey = vi.fn();

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => ({ data: [] }),
}));

vi.mock("@/hooks/useWorkflows", () => ({
	useUpdateWorkflow: () => ({ mutateAsync: mockUpdateWorkflow }),
}));

vi.mock("@/hooks/useWorkflowRoles", () => ({
	useWorkflowRoles: () => ({
		data: { role_ids: [] },
		refetch: mockWorkflowRolesRefetch,
	}),
	useAssignRolesToWorkflow: () => ({ mutateAsync: mockAssignRoles }),
	useRemoveRoleFromWorkflow: () => ({ mutateAsync: mockRemoveRole }),
}));

vi.mock("@/hooks/useWorkflowKeys", () => ({
	useWorkflowKeys: () => mockUseWorkflowKeys(),
	useCreateWorkflowKey: () => ({ mutateAsync: mockCreateKey }),
	useRevokeWorkflowKey: () => ({ mutateAsync: mockRevokeKey }),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		value,
		onChange,
	}: {
		value?: string | null;
		onChange: (value: string | null) => void;
	}) => (
		<select
			aria-label="Organization Scope"
			value={value ?? "global"}
			onChange={(event) =>
				onChange(event.currentTarget.value === "global" ? null : event.currentTarget.value)
			}
		>
			<option value="global">Global</option>
		</select>
	),
}));

vi.mock("@/components/workflows/WorkflowAccessPanel", () => ({
	WorkflowAccessPanel: ({ workflow }: { workflow: { id: string } }) => (
		<section aria-label="Workflow Access Panel">{workflow.id}</section>
	),
}));

import { WorkflowEditDialog } from "./WorkflowEditDialog";
import type { components } from "@/lib/v1";

type Workflow = components["schemas"]["WorkflowMetadata"];

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
	return {
		id: "workflow-1",
		name: "current_tool_name",
		function_name: "python_function_name",
		display_name: null,
		description: null,
		type: "tool",
		organization_id: null,
		access_level: "authenticated",
		category: "General",
		tags: [],
		parameters: [],
		execution_mode: "sync",
		timeout_seconds: 1800,
		retry_policy: null,
		endpoint_enabled: false,
		allowed_methods: ["POST"],
		disable_global_key: false,
		public_endpoint: false,
		is_tool: false,
		tool_description: null,
		cache_ttl_seconds: 300,
		time_saved: 0,
		value: 0,
		used_by_count: 0,
		source_file_path: "workflows/example.py",
		relative_file_path: "workflows/example.py",
		created_at: "2026-06-05T00:00:00Z",
		...overrides,
	} as Workflow;
}

beforeEach(() => {
	mockUpdateWorkflow.mockReset();
	mockUpdateWorkflow.mockResolvedValue({});
	mockAssignRoles.mockReset();
	mockAssignRoles.mockResolvedValue({});
	mockRemoveRole.mockReset();
	mockRemoveRole.mockResolvedValue({});
	mockWorkflowRolesRefetch.mockReset();
	mockWorkflowRolesRefetch.mockResolvedValue({ data: { role_ids: [] } });
	mockUseWorkflowKeys.mockReset();
	mockCreateKey.mockReset();
	mockRevokeKey.mockReset();
	mockUseWorkflowKeys.mockReturnValue({
		data: [],
		refetch: vi.fn(),
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

describe("WorkflowEditDialog", () => {
	it("submits an edited workflow tool name separately from the function name", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<WorkflowEditDialog
				workflow={makeWorkflow()}
				open={true}
				onOpenChange={onOpenChange}
			/>,
		);

		const toolNameInput = screen.getByLabelText(/tool name/i);
		expect(toolNameInput).toHaveValue("current_tool_name");

		await user.clear(toolNameInput);
		await user.type(toolNameInput, "renamed_tool_name");
		await user.click(screen.getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(mockUpdateWorkflow).toHaveBeenCalledTimes(1));
		expect(mockUpdateWorkflow.mock.calls[0]).toMatchObject([
			"workflow-1",
			{
				name: "renamed_tool_name",
				display_name: null,
			},
		]);
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("requires a successful role read before saving and supports retry", async () => {
		mockWorkflowRolesRefetch.mockResolvedValueOnce({ data: undefined }).mockResolvedValueOnce({ data: { role_ids: ["role-1"] } });
		const { user } = renderWithProviders(<WorkflowEditDialog workflow={makeWorkflow({ access_level: "role_based" })} open initialTab="access" onOpenChange={vi.fn()} />);
		await screen.findByText(/Could not load assigned roles/);
		expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
		await user.click(screen.getByRole("button", { name: "Retry loading roles" }));
		await waitFor(() => expect(screen.getByRole("button", { name: "Save Changes" })).toBeEnabled());
		await user.click(screen.getByRole("button", { name: "Remove role-1 role" }));
		await user.click(screen.getByRole("button", { name: "Save Changes" }));
		await waitFor(() => expect(mockRemoveRole).toHaveBeenCalledWith("workflow-1", "role-1"));
	});

	it("shows who the workflow runs as below who may start it", () => {
		renderWithProviders(<WorkflowEditDialog workflow={makeWorkflow()} open initialTab="access" onOpenChange={vi.fn()} />);
		const panel = screen.getByRole("region", { name: "Workflow Access Panel" });
		expect(panel).toHaveTextContent("workflow-1");
		expect(screen.getByText("Access Level").compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it("keeps the dialog open and prevents duplicate save while a request is pending", async () => {
		let finish!: () => void;
		mockUpdateWorkflow.mockReturnValueOnce(new Promise<void>((resolve) => { finish = resolve; }));
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(<WorkflowEditDialog workflow={makeWorkflow()} open onOpenChange={onOpenChange} />);
		await user.click(screen.getByRole("button", { name: "Save Changes" }));
		expect(screen.getByRole("button", { name: "Saving..." })).toBeDisabled();
		expect(document.querySelector('[data-slot="tabs"]')).toHaveAttribute("inert");
		await user.keyboard("{Escape}");
		expect(onOpenChange).not.toHaveBeenCalled();
		expect(mockUpdateWorkflow).toHaveBeenCalledOnce();
		finish();
		await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
	});

	it("retains the draft and focuses a persistent save error before retry", async () => {
		mockUpdateWorkflow.mockRejectedValueOnce(new Error("Synthetic update failure"));
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(<WorkflowEditDialog workflow={makeWorkflow()} open onOpenChange={onOpenChange} />);
		const name = screen.getByLabelText(/tool name/i);
		await user.clear(name);
		await user.type(name, "retained_draft");
		await user.click(screen.getByRole("button", { name: "Save Changes" }));
		const error = await screen.findByRole("alert");
		expect(error).toHaveTextContent("Synthetic update failure");
		expect(error).toHaveFocus();
		expect(name).toHaveValue("retained_draft");
		expect(onOpenChange).not.toHaveBeenCalled();
		await user.click(screen.getByRole("button", { name: "Retry save" }));
		await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
		expect(mockUpdateWorkflow).toHaveBeenCalledTimes(2);
	});

	it("resets the workflow tool name to the function name when cleared", async () => {
		const { user } = renderWithProviders(
			<WorkflowEditDialog
				workflow={makeWorkflow()}
				open={true}
				onOpenChange={vi.fn()}
			/>,
		);

		const toolNameInput = screen.getByLabelText(/tool name/i);
		await user.clear(toolNameInput);
		await user.click(screen.getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(mockUpdateWorkflow).toHaveBeenCalledTimes(1));
		expect(mockUpdateWorkflow.mock.calls[0]).toMatchObject([
			"workflow-1",
			{
				name: "python_function_name",
			},
		]);
	});

	it("exposes an accessible copy control and clears the reset timer on close", async () => {
		const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");
		const clipboard = await import("@/lib/clipboard");
		const copyToClipboard = vi
			.spyOn(clipboard, "copyToClipboard")
			.mockResolvedValueOnce(false)
			.mockResolvedValueOnce(true);
		mockUseWorkflowKeys.mockReturnValue({
			data: [{ id: "key-1", masked_key: "abcd-1234" }],
			refetch: vi.fn(),
		});
		const { user, unmount } = renderWithProviders(
			<WorkflowEditDialog
				workflow={makeWorkflow({ endpoint_enabled: true })}
				open={true}
				onOpenChange={vi.fn()}
				initialTab="endpoint"
			/>,
		);

		const copyButton = screen.getByRole("button", {
			name: "Copy cURL example",
		});
		expect(copyButton).toHaveAttribute("title", "Copy cURL example");

		await user.click(copyButton);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not copy the cURL example",
		);
		expect(
			screen.getByRole("button", { name: "Retry cURL example copy" }),
		).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Retry cURL example copy" }));
		await waitFor(() => expect(copyToClipboard).toHaveBeenCalledTimes(2));
		expect(
			screen.getByRole("button", { name: "Copied cURL example" }),
		).toBeInTheDocument();
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();

		unmount();
		expect(clearTimeoutSpy).toHaveBeenCalled();
		copyToClipboard.mockRestore();
	});
});

it("offers key-read recovery without presenting failure as an unconfigured key", async () => {
	const refetch = vi.fn();
	mockUseWorkflowKeys.mockReturnValue({ data: undefined, isError: true, refetch });
	const props = { open: true, onOpenChange: vi.fn(), workflow: makeWorkflow({ endpoint_enabled: true }), initialTab: "endpoint" };
	const { user, rerender } = renderWithProviders(<WorkflowEditDialog {...props} />);
	expect(await screen.findByText("Could not load workflow API keys.")).toBeVisible();
	expect(screen.queryByRole("button", { name: "Generate Key" })).not.toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Retry API keys" }));
	expect(refetch).toHaveBeenCalledOnce();
	mockUseWorkflowKeys.mockReturnValue({ data: [], isError: false, refetch });
	rerender(<WorkflowEditDialog {...props} />);
	expect(screen.getByRole("button", { name: "Generate Key" })).toBeVisible();
	expect(screen.getByRole("switch", { name: "Enable HTTP Endpoint" })).toBeChecked();
	expect(screen.getByRole("button", { name: "POST" })).toHaveAttribute("aria-pressed", "true");
});


it("protects a pending key rotation and retries creation without repeating revocation", async () => {
	mockUseWorkflowKeys.mockReturnValue({ data: [{ id: "workflow-1", masked_key: "demo…only" }], refetch: vi.fn() });
	mockRevokeKey.mockResolvedValue(undefined);
	let rejectCreate!: (reason: Error) => void;
	mockCreateKey.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectCreate = reject; })).mockResolvedValueOnce({ raw_key: "synthetic-example" });
	const onOpenChange = vi.fn();
	const { user } = renderWithProviders(<WorkflowEditDialog open onOpenChange={onOpenChange} workflow={makeWorkflow({ endpoint_enabled: true })} initialTab="endpoint" />);
	await user.click(screen.getByRole("button", { name: "Regenerate API key" }));
	await waitFor(() => expect(mockCreateKey).toHaveBeenCalledOnce());
	await user.keyboard("{Escape}");
	expect(onOpenChange).not.toHaveBeenCalled();
	expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
	rejectCreate(new Error("Synthetic failure"));
	const error = await screen.findByText(/The previous API key was revoked/);
	expect(error).toBeVisible();
	await user.click(screen.getByRole("button", { name: "Retry key generation" }));
	await waitFor(() => expect(screen.getByRole("textbox", { name: "Workflow API key" })).toHaveValue("synthetic-example"));
	expect(mockRevokeKey).toHaveBeenCalledOnce();
	expect(mockCreateKey).toHaveBeenCalledTimes(2);
});
