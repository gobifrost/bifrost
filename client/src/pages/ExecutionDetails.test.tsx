/**
 * Tests for admin-only controls in ExecutionDetails.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mockUseExecution = vi.fn();
vi.mock("@/hooks/useExecutions", () => ({
	useExecution: (...args: unknown[]) => mockUseExecution(...args),
	cancelExecution: vi.fn(),
}));

const mockUseWorkflowsMetadata = vi.fn();
vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: (...args: unknown[]) => mockUseWorkflowsMetadata(...args),
	executeWorkflowWithContext: vi.fn(),
}));

const mockAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

vi.mock("@/hooks/useExecutionStream", () => ({
	useExecutionStream: () => ({ isConnected: false }),
}));

vi.mock("@/stores/executionStreamStore", () => ({
	useExecutionStreamStore: () => undefined,
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
	ExecutionResultPanel: () => <div>Result</div>,
	ExecutionLogsPanel: () => <div>Logs</div>,
	ExecutionSidebar: () => <aside>Sidebar</aside>,
	ExecutionCancelDialog: () => null,
	ExecutionRerunDialog: ({ open }: { open: boolean }) =>
		open ? <div role="dialog">Rerun dialog</div> : null,
	ExecutionMetadataBar: ({ workflowName }: { workflowName: string }) => (
		<div>{workflowName}</div>
	),
	ExecutionStatusBadge: ({ status }: { status: string }) => (
		<span>{status}</span>
	),
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

beforeEach(() => {
	vi.clearAllMocks();
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
});

async function renderPage() {
	const { ExecutionDetails } = await import("./ExecutionDetails");
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

		expect(screen.getByRole("button", { name: /rerun/i })).toBeInTheDocument();
		expect(mockUseWorkflowsMetadata).toHaveBeenCalledWith({
			enabled: true,
		});
	});
});
