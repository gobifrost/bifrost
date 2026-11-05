import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";
import { WorkflowSelectorDialog } from "./WorkflowSelectorDialog";
import * as apiClient from "@/lib/api-client";

const mocks = vi.hoisted(() => ({
	refetch: vi.fn(),
	fetchWorkflowRolesBatch: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: vi.fn(),
	},
}));

vi.mock("@/hooks/useWorkflowRoles", () => ({
	fetchWorkflowRolesBatch: (...args: unknown[]) =>
		mocks.fetchWorkflowRolesBatch(...args),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [{ id: "org-1", name: "Operations" }],
	}),
}));
const mockUseAuthorization = vi.fn();
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockUseAuthorization(),
}));

function makeWorkflow(overrides: Record<string, unknown> = {}) {
	return {
		id: "wf-1",
		name: "Workflow with a deliberately long name that should wrap cleanly",
		description:
			"A long description that should wrap instead of clipping on small screens.",
		organization_id: "org-1",
		type: "workflow",
		...overrides,
	};
}

beforeEach(() => {
	mocks.refetch.mockReset();
	mocks.fetchWorkflowRolesBatch.mockReset();
	mockUseAuthorization.mockReturnValue({ authorization: undefined });
});

describe("WorkflowSelectorDialog", () => {
	it("renders selectable workflows and confirms role assignment", async () => {
		const useQuery = (apiClient.$api.useQuery as unknown as ReturnType<typeof vi.fn>);
		useQuery.mockReturnValue({
			data: [makeWorkflow()],
			isLoading: false,
			error: null,
			refetch: mocks.refetch,
		} as never);
		mocks.fetchWorkflowRolesBatch.mockResolvedValue(
			new Map([["wf-1", []]]),
		);

		const onSelect = vi.fn();
		const onOpenChange = vi.fn();

		renderWithProviders(
			<WorkflowSelectorDialog
				open={true}
				onOpenChange={onOpenChange}
				entityRoles={[{ id: "role-1", name: "Approvers" }]}
				mode="multi"
				selectedWorkflowIds={[]}
				onSelect={onSelect}
			/>,
		);

		await waitFor(() =>
			expect(
				screen.getByRole("button", {
					name: /workflow with a deliberately long name/i,
				}),
			).toBeInTheDocument(),
		);

		expect(screen.getByPlaceholderText("Search workflows...")).toHaveClass(
			"min-h-11",
		);

		fireEvent.click(
			screen.getByRole("button", {
				name: /workflow with a deliberately long name/i,
			}),
		);

		expect(
			screen.getByRole("button", { name: "Select & Assign Roles" }),
		).toHaveClass("min-h-11");

		fireEvent.click(screen.getByRole("button", { name: "Select & Assign Roles" }));

		expect(onSelect).toHaveBeenCalledWith(["wf-1"], true);
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("shows a retryable load error", async () => {
		const useQuery = (apiClient.$api.useQuery as unknown as ReturnType<typeof vi.fn>);
		useQuery.mockReturnValue({
			data: undefined,
			isLoading: false,
			error: new Error("boom"),
			refetch: mocks.refetch,
		} as never);

		renderWithProviders(
			<WorkflowSelectorDialog
				open={true}
				onOpenChange={vi.fn()}
				entityRoles={[]}
				mode="single"
				selectedWorkflowIds={[]}
				onSelect={vi.fn()}
			/>,
		);

		expect(await screen.findByText("Failed to load workflows")).toBeInTheDocument();
		fireEvent.click(screen.getByRole("button", { name: "Retry" }));
		expect(mocks.refetch).toHaveBeenCalledTimes(1);
	});
});
