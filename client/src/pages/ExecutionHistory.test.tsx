/**
 * Tests for ExecutionHistory.
 *
 * The page composes a lot of hooks (auth, scope store, organizations,
 * execution list + stream, search filter). We mock them all at module
 * scope so we can drive the component with deterministic data.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockUseExecutions = vi.fn();
const mockCancelExecution = vi.fn();
vi.mock("@/hooks/useExecutions", () => ({
	useExecutions: (...args: unknown[]) => mockUseExecutions(...args),
	cancelExecution: (...args: unknown[]) => mockCancelExecution(...args),
}));

vi.mock("@/hooks/useExecutionStream", () => ({
	useExecutionHistory: () => {},
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/stores/scopeStore", () => ({
	useScopeStore: (
		selector: (s: {
			scope: { type: string; orgId: string | null; orgName: string | null };
			isGlobalScope: boolean;
		}) => unknown,
	) =>
		selector({
			scope: { type: "global", orgId: null, orgName: null },
			isGlobalScope: true,
		}),
}));

const mockAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

// Passthrough search — filter by substring across the configured keys.
vi.mock("@/hooks/useSearch", () => ({
	useSearch: <T,>(items: T[]): T[] => items,
}));

const mockApiPost = vi.fn();
const mockApiGet = vi.fn();
vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: (...args: unknown[]) => mockApiGet(...args),
		POST: (...args: unknown[]) => mockApiPost(...args),
	},
	$api: {
		useQuery: vi.fn(() => ({ data: undefined, isLoading: false })),
		useMutation: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
	},
	authFetch: vi.fn(),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

// Stub heavy children so they don't explode without their own data deps.
vi.mock("@/pages/ExecutionHistory/components/ExecutionDrawer", () => ({
	ExecutionDrawer: () => null,
}));

vi.mock("@/pages/ExecutionHistory/components/LogsView", () => ({
	LogsView: () => null,
}));

vi.mock("@/components/agents/AgentRunsPanel", () => ({
	AgentRunsPanel: () => null,
}));

vi.mock("@/components/forms/WorkflowSelector", () => ({
	WorkflowSelector: () => <div aria-label="Workflow selector" />,
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));

vi.mock("@/components/search/SearchBox", () => ({
	SearchBox: () => null,
}));

vi.mock("@/components/ui/date-range-picker", () => ({
	DateRangePicker: () => null,
}));

// -----------------------------------------------------------------------------
// Fixtures
// -----------------------------------------------------------------------------

type ExecRow = {
	execution_id: string;
	workflow_name: string;
	workflow_id: string | null;
	org_id: string | null;
	form_id: string | null;
	executed_by: string;
	executed_by_name: string;
	status: string;
	input_data: Record<string, unknown>;
	started_at: string | null;
	completed_at: string | null;
	scheduled_at?: string | null;
	time_saved: number;
	value: number;
};

function makeRow(overrides: Partial<ExecRow> = {}): ExecRow {
	return {
		execution_id: "11111111-1111-1111-1111-111111111111",
		workflow_name: "test-workflow",
		workflow_id: null,
		org_id: null,
		form_id: null,
		executed_by: "user-1",
		executed_by_name: "Test User",
		status: "Success",
		input_data: {},
		started_at: "2026-04-23T10:00:00Z",
		completed_at: "2026-04-23T10:00:05Z",
		scheduled_at: null,
		time_saved: 0,
		value: 0,
		...overrides,
	};
}

const mockRefetch = vi.fn();

beforeEach(() => {
	vi.clearAllMocks();
	mockAuth.mockReturnValue({
		isPlatformAdmin: false,
		user: { id: "user-1", email: "u@example.com" },
	});
	mockUseExecutions.mockReturnValue({
		data: { executions: [], continuation_token: null },
		isFetching: false,
		refetch: mockRefetch,
	});
});

async function renderPage(initialEntries?: string[]) {
	const { ExecutionHistory } = await import("./ExecutionHistory");
	return renderWithProviders(<ExecutionHistory />, { initialEntries });
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("ExecutionHistory — status filter", () => {
	it("exposes a Scheduled option in the status tabs", async () => {
		await renderPage();
		// The page uses Tabs as the status filter. Each TabsTrigger renders as
		// role="tab". We assert a Scheduled tab is present.
		expect(
			screen.getByRole("tab", { name: /^Scheduled$/i }),
		).toBeInTheDocument();
	});
});

describe("ExecutionHistory — workflow filter visibility", () => {
	it("hides the workflow selector and ignores workflow query params for regular users", async () => {
		await renderPage(["/history?workflow=workflow-1"]);

		expect(
			screen.queryByLabelText(/workflow selector/i),
		).not.toBeInTheDocument();
		expect(mockUseExecutions).toHaveBeenLastCalledWith(
			undefined,
			expect.not.objectContaining({ workflow_id: "workflow-1" }),
			undefined,
		);
	});

	it("shows the workflow selector for platform admins", async () => {
		mockAuth.mockReturnValue({
			isPlatformAdmin: true,
			user: { id: "admin-1", email: "admin@example.com" },
		});

		await renderPage();

		expect(screen.getByLabelText(/workflow selector/i)).toBeInTheDocument();
	});
});

describe("ExecutionHistory — cancel row action", () => {
	const scheduledRow = makeRow({
		execution_id: "22222222-2222-2222-2222-222222222222",
		workflow_name: "scheduled-run",
		status: "Scheduled",
		started_at: null,
		completed_at: null,
		scheduled_at: "2030-01-01T00:00:00Z",
	});

	beforeEach(() => {
		mockUseExecutions.mockReturnValue({
			data: {
				executions: [scheduledRow],
				continuation_token: null,
			},
			isFetching: false,
			refetch: mockRefetch,
		});
	});

	it("shows a Cancel button on Scheduled rows, opens a confirm dialog, and calls the cancel endpoint", async () => {
		mockApiPost.mockResolvedValue({
			data: {
				execution_id: scheduledRow.execution_id,
				status: "Cancelled",
			},
			error: undefined,
		});

		const { user } = await renderPage();

		// The Cancel row button is identified by its title attribute.
		const cancelBtn = await screen.findByTitle(/Cancel scheduled execution/i);
		await user.click(cancelBtn);

		// Confirm dialog appears.
		const dialog = await screen.findByRole("alertdialog");
		expect(
			within(dialog).getByRole("heading", {
				name: /cancel scheduled run/i,
			}),
		).toBeInTheDocument();
		expect(within(dialog).getByText(/scheduled-run/)).toBeInTheDocument();

		// Confirm the cancel.
		await user.click(
			within(dialog).getByRole("button", { name: /confirm cancel/i }),
		);

		await waitFor(() => {
			expect(mockApiPost).toHaveBeenCalledWith(
				"/api/workflows/executions/{execution_id}/cancel",
				expect.objectContaining({
					params: {
						path: { execution_id: scheduledRow.execution_id },
					},
				}),
			);
		});
	});

	it("shows a toast and does not crash when the cancel endpoint returns 409", async () => {
		const { toast } = await import("sonner");
		mockApiPost.mockResolvedValue({
			data: undefined,
			error: {
				status: 409,
				detail: "Execution is not Scheduled (current status: Pending)",
			},
			response: { status: 409 },
		});

		const { user } = await renderPage();

		const cancelBtn = await screen.findByTitle(/Cancel scheduled execution/i);
		await user.click(cancelBtn);

		const dialog = await screen.findByRole("alertdialog");
		await user.click(
			within(dialog).getByRole("button", { name: /confirm cancel/i }),
		);

		await waitFor(() => {
			expect(toast.error).toHaveBeenCalled();
		});
	});
});
