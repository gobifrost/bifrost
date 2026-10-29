import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, fireEvent, waitFor } from "@/test-utils";

const mockNavigate = vi.fn();
const mockParams = vi.fn();
const mockLocation = vi.fn();
const mockUseAuth = vi.fn();
const mockCanAt = vi.fn();
const mockUseOrgScope = vi.fn();
const mockUseForm = vi.fn();
const mockUseCreateForm = vi.fn();
const mockUseUpdateForm = vi.fn();
const mockUseWorkflowsMetadata = vi.fn();
const mockExecuteFormStartup = vi.fn();
const mockAssignRolesToForm = vi.fn();

vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return {
		...actual,
		useNavigate: () => mockNavigate,
		useParams: () => mockParams(),
		useLocation: () => mockLocation(),
	};
});

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ canAt: mockCanAt }),
}));

vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => mockUseOrgScope(),
}));

vi.mock("@/hooks/useForms", () => ({
	useForm: () => mockUseForm(),
	useCreateForm: () => mockUseCreateForm(),
	useUpdateForm: () => mockUseUpdateForm(),
	executeFormStartup: (...args: unknown[]) => mockExecuteFormStartup(...args),
}));

vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => mockUseWorkflowsMetadata(),
}));

vi.mock("@/hooks/useRoles", () => ({
	assignRolesToForm: (...args: unknown[]) => mockAssignRolesToForm(...args),
}));

vi.mock("@/components/solutions/SolutionManagedBanner", () => ({
	SolutionManagedBanner: () => null,
}));
vi.mock("@/components/forms/FormInfoDialog", () => ({
	FormInfoDialog: ({
		onSave,
	}: {
		onSave: (
			values: import("@/components/forms/FormInfoDialog").FormInfoValues,
		) => void;
	}) => (
		<button
			onClick={() =>
				onSave({
					name: "New intake",
					description: "",
					workflow_id: "wf-1",
					launch_workflow_id: "",
					default_launch_params: {},
					access_level: "role_based",
					role_ids: ["role-1"],
					organization_id: "org-1",
				})
			}
		>
			Configure test form
		</button>
	),
}));
vi.mock("@/components/forms/FormShareDialog", () => ({
	FormShareDialog: () => null,
}));
vi.mock("@/components/forms/FieldsPanelDnD", () => ({
	FieldsPanelDnD: ({
		setFields,
	}: {
		setFields: (fields: import("@/lib/client-types").FormField[]) => void;
	}) => (
		<button
			onClick={() =>
				setFields([{ name: "request", label: "Request", type: "text" }])
			}
		>
			Add test field
		</button>
	),
}));
vi.mock("@/components/forms/FormPreview", () => ({
	FormPreview: () => <div>Preview panel</div>,
}));
vi.mock("@/components/workflows/WorkflowParametersForm", () => ({
	WorkflowParametersForm: ({
		onExecute,
		isExecuting,
	}: {
		onExecute: (params: Record<string, unknown>) => Promise<void>;
		isExecuting: boolean;
	}) => (
		<button
			disabled={isExecuting}
			onClick={() => void onExecute({ review: "entered parameter" })}
		>
			Run test parameters
		</button>
	),
}));
vi.mock("@/components/ui/context-viewer", () => ({
	ContextViewer: () => <div>Context viewer</div>,
}));
vi.mock("sonner", () => ({
	toast: {
		error: vi.fn(),
		info: vi.fn(),
		success: vi.fn(),
	},
}));

import { FormBuilder } from "./FormBuilder";

beforeEach(() => {
	mockNavigate.mockClear();
	mockCanAt.mockReturnValue(true);
	mockParams.mockReturnValue({ formId: "form-1" });
	mockLocation.mockReturnValue({ search: "" });
	mockUseAuth.mockReturnValue({
		user: { organizationId: "org-1" },
		isPlatformAdmin: false,
	});
	mockUseOrgScope.mockReturnValue({
		scope: { orgId: "org-1" },
	});
	mockUseForm.mockReturnValue({
		data: {
			id: "form-1",
			name: "Customer Intake",
			description: "Tell us what you need",
			workflow_id: "wf-1",
			launch_workflow_id: "launch-1",
			launch_workflow_parameters: [],
			form_schema: { fields: [] },
			is_solution_managed: false,
			access_level: "role_based",
			organization_id: "org-1",
			default_launch_params: {},
		},
	});
	mockUseCreateForm.mockReturnValue({
		mutateAsync: vi.fn(),
		isPending: false,
	});
	mockUseUpdateForm.mockReturnValue({
		mutateAsync: vi.fn(),
		isPending: false,
	});
	mockUseWorkflowsMetadata.mockReturnValue({ data: { workflows: [] } });
	mockExecuteFormStartup.mockResolvedValue({ results: [] });
	mockAssignRolesToForm.mockReset().mockResolvedValue(undefined);
});

describe("FormBuilder header controls", () => {
	it("shows the form logo editor for saved editable forms", () => {
		renderWithProviders(<FormBuilder />, {
			initialEntries: ["/forms/form-1"],
		});

		expect(
			screen.getByRole("button", { name: "Edit form logo" }),
		).toBeInTheDocument();
	});

	it("hides the form logo editor for solution-managed forms", () => {
		mockUseForm.mockReturnValue({
			data: {
				id: "form-1",
				name: "Customer Intake",
				description: "Tell us what you need",
				workflow_id: "wf-1",
				launch_workflow_id: "launch-1",
				launch_workflow_parameters: [],
				form_schema: { fields: [] },
				is_solution_managed: true,
				access_level: "role_based",
				organization_id: "org-1",
				default_launch_params: {},
			},
		});

		renderWithProviders(<FormBuilder />, {
			initialEntries: ["/forms/form-1"],
		});

		expect(
			screen.queryByRole("button", { name: "Edit form logo" }),
		).not.toBeInTheDocument();
	});

	it("uses 44px controls for the builder actions", () => {
		renderWithProviders(<FormBuilder />, {
			initialEntries: ["/forms/form-1"],
		});

		expect(
			screen.getByRole("button", { name: "Back to Forms" }),
		).toHaveAttribute("data-size", "icon-lg");
		expect(
			screen.getByRole("button", { name: "Edit Info" }),
		).toHaveAttribute("data-size", "icon-lg");
		expect(
			screen.getByRole("button", { name: "Show Context" }),
		).toHaveAttribute("data-size", "icon-lg");
		expect(
			screen.getByRole("button", { name: "Share Form" }),
		).toHaveAttribute("data-size", "icon-lg");
		expect(
			screen.getByRole("button", { name: "Test Launch Workflow" }),
		).toHaveAttribute("data-size", "icon-lg");
		expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute(
			"data-size",
			"lg",
		);
	});

	it("keeps back navigation anchored to the solution return route", async () => {
		mockLocation.mockReturnValue({ search: "?from=solution:solution-1" });
		const { user } = renderWithProviders(<FormBuilder />, {
			initialEntries: ["/forms/form-1?from=solution:solution-1"],
		});

		await user.click(
			screen.getByRole("button", { name: "Back to Solution" }),
		);

		expect(mockNavigate).toHaveBeenCalledWith("/solutions/solution-1");
	});
});

describe("FormBuilder save recovery", () => {
	it("reuses the created form when role assignment fails and save is retried", async () => {
		mockParams.mockReturnValue({});
		mockUseForm.mockReturnValue({ data: undefined });
		const create = vi.fn().mockResolvedValue({ id: "created-form" });
		const update = vi.fn().mockResolvedValue({ id: "created-form" });
		mockUseCreateForm.mockReturnValue({
			mutateAsync: create,
			isPending: false,
		});
		mockUseUpdateForm.mockReturnValue({
			mutateAsync: update,
			isPending: false,
		});
		mockAssignRolesToForm.mockRejectedValueOnce(
			new Error("Role assignment failed"),
		);
		renderWithProviders(<FormBuilder />);
		fireEvent.click(
			screen.getByRole("button", { name: "Configure test form" }),
		);
		fireEvent.click(screen.getByRole("button", { name: "Add test field" }));
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		expect(
			await screen.findByText(
				"The form is saved, but its access roles could not be updated. Retry to finish saving.",
			),
		).toBeVisible();
		expect(mockNavigate).not.toHaveBeenCalled();
		fireEvent.click(screen.getByRole("button", { name: /retry save/i }));
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/forms"),
		);
		expect(create).toHaveBeenCalledTimes(1);
		expect(update).toHaveBeenCalledWith(
			expect.objectContaining({
				params: { path: { form_id: "created-form" } },
			}),
		);
		expect(mockAssignRolesToForm).toHaveBeenNthCalledWith(
			2,
			"created-form",
			["role-1"],
		);
	});
});

it("retains the launch dialog on failure and passes entered parameters on retry", async () => {
	mockUseWorkflowsMetadata.mockReturnValue({
		data: {
			workflows: [
				{
					name: "launch-1",
					parameters: [{ name: "review", type: "string" }],
				},
			],
		},
	});
	mockExecuteFormStartup
		.mockRejectedValueOnce(new Error("Synthetic launch failed"))
		.mockResolvedValueOnce({ result: { review: "success" } });
	const { user } = renderWithProviders(<FormBuilder />);
	await user.click(
		screen.getByRole("button", { name: "Test Launch Workflow" }),
	);
	await user.click(
		screen.getByRole("button", { name: "Run test parameters" }),
	);
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Synthetic launch failed",
	);
	expect(
		screen.getByRole("dialog", { name: "Test Launch Workflow" }),
	).toBeVisible();
	await user.click(
		screen.getByRole("button", { name: "Run test parameters" }),
	);
	expect(
		await screen.findByRole("dialog", {
			name: "Launch Workflow Test Results",
		}),
	).toBeVisible();
	expect(mockExecuteFormStartup).toHaveBeenLastCalledWith("form-1", {
		review: "entered parameter",
	});
	expect(
		screen.queryByRole("dialog", { name: "Test Launch Workflow" }),
	).not.toBeInTheDocument();
});

it("does not expose an empty editor when an existing form fails to load", async () => {
	const refetch = vi.fn();
	mockUseForm.mockReturnValue({
		data: undefined,
		isLoading: false,
		error: new Error("Unavailable"),
		refetch,
	});
	const { user } = renderWithProviders(<FormBuilder />);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Could not load this form for editing.",
	);
	expect(screen.queryByText("Add test field")).not.toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Retry form" }));
	expect(refetch).toHaveBeenCalledOnce();
});

it("retains the loaded designer but blocks launch testing when workflow metadata fails", async () => {
	const refetch = vi.fn();
	mockUseWorkflowsMetadata.mockReturnValue({
		data: { workflows: [] },
		isError: true,
		isFetching: false,
		refetch,
	});
	const { user } = renderWithProviders(<FormBuilder />);
	expect(screen.getByText("Add test field")).toBeVisible();
	expect(
		screen.getByRole("button", { name: "Test Launch Workflow" }),
	).toBeDisabled();
	await user.click(screen.getByRole("button", { name: "Retry workflows" }));
	expect(refetch).toHaveBeenCalledOnce();
});

it("preserves loaded role assignments in an existing-form patch without additive calls", async () => {
	const update = vi.fn().mockResolvedValue({ id: "form-1" });
	const existing = mockUseForm().data;
	mockUseForm.mockReturnValue({
		data: {
			...existing,
			role_ids: ["existing-role"],
			form_schema: {
				fields: [{ name: "summary", label: "Summary", type: "text" }],
			},
		},
	});
	mockUseUpdateForm.mockReturnValue({
		mutateAsync: update,
		isPending: false,
	});
	const { user } = renderWithProviders(<FormBuilder />);
	await user.click(screen.getByRole("button", { name: "Save" }));
	expect(update).toHaveBeenCalledWith(
		expect.objectContaining({
			body: expect.objectContaining({
				role_ids: ["existing-role"],
				clear_roles: false,
			}),
		}),
	);
	expect(mockAssignRolesToForm).not.toHaveBeenCalled();
});

it("returns to the originating solution after saving from a solution route", async () => {
	mockLocation.mockReturnValue({ search: "?from=solution:solution-1" });
	const update = vi.fn().mockResolvedValue({ id: "form-1" });
	const existing = mockUseForm().data;
	mockUseForm.mockReturnValue({
		data: {
			...existing,
			role_ids: ["existing-role"],
			form_schema: {
				fields: [{ name: "summary", label: "Summary", type: "text" }],
			},
		},
	});
	mockUseUpdateForm.mockReturnValue({
		mutateAsync: update,
		isPending: false,
	});
	const { user } = renderWithProviders(<FormBuilder />, {
		initialEntries: ["/forms/form-1?from=solution:solution-1"],
	});

	await user.click(screen.getByRole("button", { name: "Save" }));

	await waitFor(() =>
		expect(mockNavigate).toHaveBeenCalledWith("/solutions/solution-1"),
	);
});

it("disables editing when a named write grant is held only at another organization", async () => {
	mockCanAt.mockImplementation(
		(_permission: string, target: { kind: string; id?: string }) =>
			target.id === "org-2",
	);
	renderWithProviders(<FormBuilder />);
	fireEvent.click(
		screen.getByRole("button", { name: "Configure test form" }),
	);
	fireEvent.click(screen.getByRole("button", { name: "Add test field" }));
	expect(screen.getByRole("button", { name: /^Save$/ })).toBeDisabled();
	expect(screen.getByTitle("Edit Info")).toBeDisabled();
	expect(mockCanAt).toHaveBeenCalledWith("forms.readwrite", {
		kind: "org",
		id: "org-1",
	});
});
