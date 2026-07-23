/**
 * Tests for ExecuteWorkflow.
 *
 * The page composes the workflow-metadata hook and the execute-workflow
 * mutation, plus the ScheduleControls component. We mock the workflow
 * hooks so we can drive the component with deterministic data and
 * assert on the mutation body.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { ExecuteWorkflow } from "./ExecuteWorkflow";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockMutateAsync = vi.fn();
const mockUseWorkflowsMetadata = vi.fn();
vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: (...args: unknown[]) =>
		mockUseWorkflowsMetadata(...args),
	useExecuteWorkflow: () => ({
		mutateAsync: mockMutateAsync,
		isPending: false,
	}),
}));

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
	const actual = await vi.importActual<typeof import("react-router-dom")>(
		"react-router-dom",
	);
	return {
		...actual,
		useNavigate: () => mockNavigate,
		useParams: () => ({ workflowName: "test-workflow" }),
	};
});

const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => mockToastSuccess(...args),
		error: (...args: unknown[]) => mockToastError(...args),
	},
}));

// -----------------------------------------------------------------------------
// Fixtures
// -----------------------------------------------------------------------------

const WORKFLOW = {
	id: "11111111-1111-1111-1111-111111111111",
	name: "test-workflow",
	description: "A test workflow",
	parameters: [],
};

beforeEach(() => {
	vi.clearAllMocks();
	mockUseWorkflowsMetadata.mockReturnValue({
		data: { workflows: [WORKFLOW] },
		isLoading: false,
	});
	// Default mutation resolves as a normal completed run.
	mockMutateAsync.mockResolvedValue({
		execution_id: "abc-exec-id",
		status: "Success",
	});
});

async function renderPage() {
	return renderWithProviders(<ExecuteWorkflow />);
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("ExecuteWorkflow — run-now path (no schedule)", () => {
	it(
		"submits a body without scheduled_at or delay_seconds when the schedule checkbox is untouched",
		async () => {
			const { user } = await renderPage();

			await user.click(
				await screen.findByRole("button", {
					name: /execute workflow/i,
				}),
			);

			await waitFor(() => {
				expect(mockMutateAsync).toHaveBeenCalledTimes(1);
			});

			const body = mockMutateAsync.mock.calls[0][0].body as Record<
				string,
				unknown
			>;
			expect(body).not.toHaveProperty("scheduled_at");
			expect(body).not.toHaveProperty("delay_seconds");
			expect(body).toMatchObject({
				workflow_id: WORKFLOW.id,
				transient: false,
			});

			// Non-scheduled response goes to the details page.
			await waitFor(() => {
				expect(mockNavigate).toHaveBeenCalledWith(
					"/history/abc-exec-id",
					expect.any(Object),
				);
			});
		},
		15000,
	);
});

describe("ExecuteWorkflow — scheduled path", () => {
	it(
		"sends delay_seconds: 3600 when the user picks 'In 1 hour' and submits",
		async () => {
			const { user } = await renderPage();

			await user.click(
				await screen.findByRole("checkbox", {
					name: /schedule for later/i,
				}),
			);
			await user.click(
				screen.getByRole("button", { name: /in 1 hour/i }),
			);

			await user.click(
				screen.getByRole("button", { name: /execute workflow/i }),
			);

			await waitFor(() => {
				expect(mockMutateAsync).toHaveBeenCalledTimes(1);
			});

			const body = mockMutateAsync.mock.calls[0][0].body as Record<
				string,
				unknown
			>;
			expect(body.delay_seconds).toBe(3600);
			expect(body).not.toHaveProperty("scheduled_at");
		},
		15000,
	);

	it(
		"navigates to /history and toasts the scheduled time on a Scheduled response",
		async () => {
			mockMutateAsync.mockResolvedValueOnce({
				execution_id: "abc",
				status: "Scheduled",
				scheduled_at: "2026-05-01T12:00:00Z",
			});

			const { user } = await renderPage();

			await user.click(
				await screen.findByRole("checkbox", {
					name: /schedule for later/i,
				}),
			);
			await user.click(
				screen.getByRole("button", { name: /in 1 hour/i }),
			);
			await user.click(
				screen.getByRole("button", { name: /execute workflow/i }),
			);

			await waitFor(() => {
				expect(mockNavigate).toHaveBeenCalledWith("/history");
			});

			expect(mockToastSuccess).toHaveBeenCalledTimes(1);
			const toastMsg = mockToastSuccess.mock.calls[0][0] as string;
			expect(toastMsg).toMatch(/scheduled for/i);
		},
		15000,
	);
});
