import { ExecutionDetails } from "./ExecutionDetails";
/**
 * Tests for admin-only controls in ExecutionDetails.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router-dom";
import {
	makeQueryClient,
	renderWithProviders,
	screen,
	waitFor,
} from "@/test-utils";

const mockUseExecution = vi.fn();
const mockUseExecutionStream = vi.fn();
let mockStreamState: unknown;
vi.mock("@/hooks/useExecutions", () => ({
	useExecution: (...args: unknown[]) => mockUseExecution(...args),
	cancelExecution: vi.fn(),
}));

const mockUseWorkflowsMetadata = vi.fn();
vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: (...args: unknown[]) =>
		mockUseWorkflowsMetadata(...args),
	executeWorkflowWithContext: vi.fn(),
}));

const mockAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

vi.mock("@/hooks/useExecutionStream", () => ({
	useExecutionStream: (...args: unknown[]) => mockUseExecutionStream(...args),
}));

vi.mock("@/stores/executionStreamStore", () => ({
	useExecutionStreamStore: (
		selector?: (state: { streams: Record<string, unknown> }) => unknown,
	) => {
		const state = mockStreamState
			? {
					streams: {
						"11111111-1111-1111-1111-111111111111": mockStreamState,
					},
				}
			: { streams: {} };
		return selector ? selector(state) : state;
	},
}));

vi.mock("@/stores/editorStore", () => ({
	useEditorStore: (selector: (state: Record<string, unknown>) => unknown) =>
		selector({
			openFileInTab: vi.fn(),
			openEditor: vi.fn(),
			setSidebarPanel: vi.fn(),
			minimizeEditor: vi.fn(),
		}),
}));

vi.mock("@/services/fileService", () => ({
	fileService: { getFileMetadata: vi.fn() },
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/components/PageLoader", () => ({
	PageLoader: () => <div>Loading...</div>,
}));

vi.mock("@/components/execution", () => ({
	ExecutionResultPanel: () => (
		<div data-testid="result-panel">Result body</div>
	),
	ExecutionLogsPanel: ({ logs }: { logs?: unknown[] }) => (
		<div data-testid="logs-panel">Logs {logs?.length ?? 0}</div>
	),
	ExecutionSidebar: () => <aside>Sidebar</aside>,
	ExecutionCancelDialog: () => null,
	ExecutionRerunDialog: ({ open }: { open: boolean }) =>
		open ? <div role="dialog">Rerun dialog</div> : null,
	ExecutionMetadataBar: ({ workflowName }: { workflowName: string }) => (
		<div>{workflowName}</div>
	),
	ExecutionActivityTrace: () => <div>Live stream idle</div>,
	RunStatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
	PrettyInputDisplay: () => <div>Input</div>,
}));

const execution = {
	execution_id: "11111111-1111-1111-1111-111111111111",
	workflow_id: "22222222-2222-2222-2222-222222222222",
	workflow_name: "test-workflow",
	status: "Success",
	executed_by: "user-1",
	executed_by_name: "Test User",
	org_id: "org-1",
	org_name: "Test Org",
	form_id: null,
	input_data: {},
	result: { ok: true },
	result_type: "json",
	logs: [],
	variables: null,
	execution_context: null,
	ai_usage: [],
	ai_totals: null,
	started_at: "2026-04-23T10:00:00Z",
	completed_at: "2026-04-23T10:00:05Z",
	scheduled_at: null,
	duration_ms: 5000,
	peak_memory_bytes: null,
	cpu_total_seconds: null,
	error_message: null,
};
const executionQueryKey = [
	"get",
	"/api/executions/{execution_id}",
	{
		params: {
			path: { execution_id: execution.execution_id },
		},
	},
];

beforeEach(() => {
	vi.clearAllMocks();
	mockStreamState = undefined;
	mockAuth.mockReturnValue({
		isPlatformAdmin: false,
		hasRole: () => false,
	});
	mockUseExecution.mockReturnValue({
		data: execution,
		isLoading: false,
		error: null,
	});
	mockUseWorkflowsMetadata.mockReturnValue({
		data: { workflows: [] },
		isLoading: false,
	});
	mockUseExecutionStream.mockReturnValue({ isConnected: false });
});

async function renderPage() {
	return renderWithProviders(
		<ExecutionDetails executionId={execution.execution_id} />,
	);
}

describe("ExecutionDetails — rerun visibility", () => {
	it("hides rerun and does not fetch workflow metadata for regular users", async () => {
		await renderPage();

		expect(
			screen.queryByRole("button", { name: /rerun/i }),
		).not.toBeInTheDocument();
		expect(mockUseWorkflowsMetadata).toHaveBeenCalledWith({
			enabled: false,
		});
	});

	it("shows rerun and fetches workflow metadata for platform admins", async () => {
		mockAuth.mockReturnValue({
			isPlatformAdmin: true,
			hasRole: () => false,
		});

		await renderPage();

		expect(
			screen.getByRole("button", { name: /rerun/i }),
		).toBeInTheDocument();
		expect(mockUseWorkflowsMetadata).toHaveBeenCalledWith({
			enabled: true,
		});
	});
});

describe("ExecutionDetails — failed-run hierarchy", () => {
	it("leads with a copyable error banner and skips the Result panel", async () => {
		mockUseExecution.mockReturnValue({
			data: {
				...execution,
				status: "Failed",
				result: null,
				result_type: null,
				error_message: "RuntimeError: boom",
			},
			isLoading: false,
			error: null,
		});

		await renderPage();

		const banner = screen.getByTestId("execution-error-banner");
		expect(banner).toHaveTextContent("This run failed");
		expect(banner).toHaveTextContent("RuntimeError: boom");
		// The stubbed Result panel must NOT render for a failed run with no
		// result — previously it produced a dead "No result returned" card.
		expect(screen.queryByTestId("result-panel")).not.toBeInTheDocument();
	});

	it("renders the Result panel for successful runs and no error banner", async () => {
		await renderPage();
		expect(screen.getByTestId("result-panel")).toBeInTheDocument();
		expect(
			screen.queryByTestId("execution-error-banner"),
		).not.toBeInTheDocument();
	});
});

describe("ExecutionDetails — result-first inspector", () => {
	it("defaults to Result before Input and Logs", async () => {
		await renderPage();

		expect(screen.getByLabelText("Execution content")).toBeInTheDocument();
		const tabs = screen.getAllByRole("tab");
		expect(tabs.map((tab) => tab.textContent)).toEqual([
			"Result",
			"Input",
			"Logs",
		]);
		expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		expect(
			screen.queryByRole("tab", { name: "Output" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("tab", { name: "Details" }),
		).not.toBeInTheDocument();
	});

	it("shows truthful active status in Result for a running run with no logs", async () => {
		mockUseExecution.mockReturnValue({
			data: {
				...execution,
				status: "Running",
				result: null,
				result_type: null,
				logs: [],
				completed_at: null,
			},
			isLoading: false,
			error: null,
		});

		await renderPage();

		expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		expect(
			screen.getByText(
				"This run is active. The result will appear here when it completes.",
			),
		).toBeInTheDocument();
		expect(screen.queryByText(/log line/)).not.toBeInTheDocument();
	});

	it("summarizes available running logs in Result without opening a log column", async () => {
		mockUseExecution.mockReturnValue({
			data: {
				...execution,
				status: "Running",
				result: null,
				result_type: null,
				logs: [
					{
						timestamp: "2026-04-23T10:00:01Z",
						level: "info",
						message: "Started",
					},
				],
				completed_at: null,
			},
			isLoading: false,
			error: null,
		});

		const { user } = await renderPage();

		expect(screen.getByText("Started")).toBeInTheDocument();
		expect(
			screen.getByRole("heading", {
				name: "Run Activity",
			}),
		).toBeInTheDocument();
		expect(screen.queryByTestId("logs-panel")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Open logs" }));
		expect(screen.getByRole("tab", { name: "Logs" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		expect(screen.getByTestId("logs-panel")).toHaveTextContent("Logs 1");
	});
});

describe("ExecutionDetails — navigation fetch gating", () => {
	const triggerState = {
		workflow_name: execution.workflow_name,
		workflow_id: execution.workflow_id,
		input_data: execution.input_data,
	};

	it("preserves the parent history route state in embedded mode", () => {
		function RouteState() {
			const location = useLocation();
			return (
				<output aria-label="Route state">
					{JSON.stringify(location.state)}
				</output>
			);
		}
		const restoreState = {
			executionHistoryRestore: {
				href: "/history?status=Success&execution=run-1",
				scrollTop: 96,
			},
		};
		renderWithProviders(
			<>
				<ExecutionDetails
					executionId={execution.execution_id}
					embedded
				/>
				<RouteState />
			</>,
			{
				initialEntries: [
					{
						pathname: "/history",
						state: restoreState,
					} as unknown as string,
				],
			},
		);
		expect(screen.getByLabelText("Route state")).toHaveTextContent(
			'"scrollTop":96',
		);
		expect(mockUseExecution).toHaveBeenCalledWith(execution.execution_id, {
			disablePolling: false,
		});
	});

	it("defers the initial API fetch for a newly triggered execution", async () => {
		mockUseExecution.mockReturnValue({
			data: undefined,
			isLoading: false,
			error: null,
			isFetching: false,
			refetch: vi.fn(),
		});
		renderWithProviders(
			<ExecutionDetails executionId={execution.execution_id} />,
			{
				initialEntries: [
					{
						pathname:
							"/history/11111111-1111-1111-1111-111111111111",
						state: triggerState,
					} as unknown as string,
				],
			},
		);

		expect(mockUseExecution).toHaveBeenCalledWith(undefined, {
			disablePolling: false,
		});
		expect(mockUseExecutionStream).toHaveBeenLastCalledWith(
			expect.objectContaining({
				executionId: execution.execution_id,
				enabled: true,
			}),
		);
	});

	it("fetches immediately on browser-back re-entry when cached execution data exists", async () => {
		const queryClient = makeQueryClient();
		queryClient.setQueryData(executionQueryKey, {
			...execution,
			status: "Running",
			completed_at: null,
		});
		renderWithProviders(
			<ExecutionDetails executionId={execution.execution_id} />,
			{
				initialEntries: [
					{
						pathname:
							"/history/11111111-1111-1111-1111-111111111111",
						state: triggerState,
					} as unknown as string,
				],
				queryClient,
			},
		);

		expect(mockUseExecution).toHaveBeenCalledWith(execution.execution_id, {
			disablePolling: false,
		});
	});

	it("does not let a stale default stream status overwrite a terminal cached status", async () => {
		mockStreamState = {
			status: "Running",
			streamingLogs: [],
			hasReceivedUpdate: false,
			isComplete: false,
		};
		const queryClient = makeQueryClient();
		queryClient.setQueryData(executionQueryKey, {
			...execution,
			status: "Success",
		});
		renderWithProviders(
			<ExecutionDetails executionId={execution.execution_id} />,
			{
				initialEntries: [
					{
						pathname:
							"/history/11111111-1111-1111-1111-111111111111",
						state: triggerState,
					} as unknown as string,
				],
				queryClient,
			},
		);

		await waitFor(() =>
			expect(queryClient.getQueryData(executionQueryKey)).toMatchObject({
				status: "Success",
			}),
		);
	});
});

describe("ExecutionDetails — compact header", () => {
	it("keeps the workflow identity and named execution actions available", async () => {
		mockAuth.mockReturnValue({
			isPlatformAdmin: true,
			hasRole: () => false,
		});
		mockUseWorkflowsMetadata.mockReturnValue({
			data: {
				workflows: [
					{
						name: execution.workflow_name,
						source_file_path: "workflows/test-workflow.ts",
					},
				],
				dataProviders: [],
			},
			isLoading: false,
		});

		await renderPage();

		expect(
			screen.getByRole("heading", { name: execution.workflow_name }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /copy execution id/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /back to history/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /editor/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /rerun/i }),
		).toBeInTheDocument();
		expect(screen.getByText("Success")).toBeInTheDocument();
	});
});

describe("ExecutionDetails — cancelled outcome", () => {
	it("does not label a cancelled run as a failure", async () => {
		mockUseExecution.mockReturnValue({
			data: {
				...execution,
				status: "Cancelled",
				error_message: "Execution was cancelled",
			},
			isLoading: false,
			error: null,
		});
		await renderPage();
		await waitFor(() =>
			expect(screen.getByText("This run was cancelled")).toBeVisible(),
		);
		expect(screen.queryByText("This run failed")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Copy cancellation message" }),
		).toBeVisible();
	});
});

describe("ExecutionDetails — embedded metrics", () => {
	it("offers more details when the only extras are measured zero metrics", async () => {
		mockAuth.mockReturnValue({
			isPlatformAdmin: true,
			hasRole: () => false,
		});
		mockUseExecution.mockReturnValue({
			data: { ...execution, peak_memory_bytes: 0, cpu_total_seconds: 0 },
			isLoading: false,
			error: null,
		});
		const { user } = renderWithProviders(
			<ExecutionDetails executionId={execution.execution_id} embedded />,
		);
		const more = screen.getByRole("button", { name: "More details" });
		await user.click(more);
		expect(more).toHaveAttribute("aria-expanded", "true");
	});
});

it("offers retry when initial execution loading fails", async () => {
	const refetch = vi.fn();
	mockUseExecution.mockReturnValue({
		data: undefined,
		isLoading: false,
		error: new Error("Synthetic failure"),
		isFetching: false,
		refetch,
	});
	const { user } = await renderPage();
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Could not load execution",
	);
	await user.click(screen.getByRole("button", { name: "Retry execution" }));
	expect(refetch).toHaveBeenCalledOnce();
});

it("retains cached execution content after a failed refresh", async () => {
	mockUseExecution.mockReturnValue({
		data: execution,
		isLoading: false,
		error: new Error("Synthetic failure"),
		isFetching: true,
		refetch: vi.fn(),
	});
	await renderPage();
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Could not refresh execution",
	);
	expect(
		screen.getByRole("heading", { name: "test-workflow" }),
	).toBeVisible();
	expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
});

it("keeps the embedded run identity and status visible without workspace actions", async () => {
	mockAuth.mockReturnValue({
		isPlatformAdmin: false,
		hasRole: (role: string) => role === "EmbedUser",
	});
	await renderPage();
	expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
		execution.workflow_name,
	);
	expect(screen.getByRole("status")).toHaveTextContent(execution.status);
	expect(
		screen.queryByRole("button", { name: "Back to history" }),
	).not.toBeInTheDocument();
	expect(
		screen.queryByRole("button", { name: "Copy execution ID" }),
	).not.toBeInTheDocument();
});

describe("Execution review continuity", () => {
	it("keeps readable activity in Result after the run completes", async () => {
		const logs = [
			{ sequence: 1, message: "Verified ownership", level: "info" },
		];
		mockUseExecution.mockReturnValue({
			data: {
				...execution,
				status: "Running",
				completed_at: null,
				result: null,
				logs,
			},
			isLoading: false,
			error: null,
		});
		const { rerender } = await renderPage();
		expect(screen.getByText("Verified ownership")).toBeInTheDocument();
		mockUseExecution.mockReturnValue({
			data: { ...execution, logs },
			isLoading: false,
			error: null,
		});
		rerender(<ExecutionDetails executionId={execution.execution_id} />);
		expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		expect(screen.getByText("Verified ownership")).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: "Run Activity" }),
		).toBeInTheDocument();
	});

	it("offers a labeled rerun in embedded mode without a portal target", async () => {
		mockAuth.mockReturnValue({
			isPlatformAdmin: true,
			hasRole: () => false,
		});
		const { user } = renderWithProviders(
			<ExecutionDetails executionId={execution.execution_id} embedded />,
		);
		await user.click(
			screen.getByRole("button", { name: "Rerun execution" }),
		);
		expect(screen.getByRole("dialog")).toHaveTextContent("Rerun dialog");
	});
});
