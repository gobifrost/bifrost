import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUpdateWorkflow = vi.fn();
const mockAssignRoles = vi.fn();
const mockRemoveRole = vi.fn();
const mockWorkflowRolesRefetch = vi.fn();

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
	useWorkflowKeys: () => ({ data: [], refetch: vi.fn() }),
	useCreateWorkflowKey: () => ({ mutateAsync: vi.fn() }),
	useRevokeWorkflowKey: () => ({ mutateAsync: vi.fn() }),
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
});
