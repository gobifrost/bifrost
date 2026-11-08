/**
 * Tests for SummaryBackfillButton — specifically the terminal-state UX.
 *
 * The regressions we're guarding against:
 *   - Terminal transitions (complete / cancelled / failed) must NOT auto-dismiss
 *     the card — the user has to click X. This is how the failure count stays
 *     visible after the last run lands.
 *   - Dismissing a terminal card writes the job_id to sessionStorage so it
 *     doesn't flash back into view across re-renders within the same tab.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

type BackfillEstimateResponse = {
	eligible: number;
	estimated_cost_usd: string;
	cost_basis: "history" | "fallback";
	job_id?: string | null;
	queued?: number;
};

const hoisted = vi.hoisted(() => {
	const state: {
		lastWsCallback: ((update: unknown) => void) | null;
		backfillCalls: Array<{
			body: unknown;
			options: {
				onSuccess?: (data: BackfillEstimateResponse) => void;
				onError?: (error: unknown) => void;
			};
		}>;
		cancelCalls: Array<{
			params: { path: { job_id: string } };
			options: {
				onError?: (error: unknown) => void;
			};
		}>;
	} = { lastWsCallback: null, backfillCalls: [], cancelCalls: [] };
	return {
		state,
		mockUseSummaryBackfillJob: vi.fn(),
		mockUseSummaryBackfillJobs: vi.fn(),
		mockUseBackfillEligible: vi.fn(),
		mockCancelMutate: vi.fn(
			(
				params: { params: { path: { job_id: string } } },
				options: { onError?: (error: unknown) => void },
			) => {
				state.cancelCalls.push({
					params: params.params,
					options,
				});
			},
		),
		mockBackfillMutate: vi.fn(
			(
				body: { body: unknown },
				options: {
					onSuccess?: (data: BackfillEstimateResponse) => void;
					onError?: (error: unknown) => void;
				},
			) => {
				state.backfillCalls.push({
					body: body.body,
					options,
				});
			},
		),
		mockOnSummaryBackfillUpdate: vi.fn(
			(_jobId: string, cb: (update: unknown) => void) => {
				state.lastWsCallback = cb;
				return () => {
					state.lastWsCallback = null;
				};
			},
		),
	};
});

vi.mock("@/services/agentRuns", () => ({
	useBackfillSummaries: () => ({
		mutate: hoisted.mockBackfillMutate,
		isPending: false,
	}),
	useBackfillEligible: (
		agentId: string | undefined,
		promptVersionBelow?: string,
	) => hoisted.mockUseBackfillEligible(agentId, promptVersionBelow),
	useCancelBackfillJob: () => ({
		mutate: hoisted.mockCancelMutate,
		isPending: false,
	}),
	useSummaryBackfillJob: (jobId: string | undefined) =>
		hoisted.mockUseSummaryBackfillJob(jobId),
	useSummaryBackfillJobs: (activeOnly: boolean) =>
		hoisted.mockUseSummaryBackfillJobs(activeOnly),
}));

vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connectInBackground: vi.fn(),
		onSummaryBackfillUpdate: hoisted.mockOnSummaryBackfillUpdate,
	},
}));

const {
	state: wsState,
	mockUseSummaryBackfillJob,
	mockUseSummaryBackfillJobs,
	mockUseBackfillEligible,
	mockCancelMutate,
	mockBackfillMutate,
} = hoisted;

import { SummaryBackfillButton } from "./SummaryBackfillButton";
import { act } from "react";

const JOB_ID = "00000000-0000-0000-0000-000000000aaa";

beforeEach(() => {
	sessionStorage.clear();
	mockUseSummaryBackfillJob.mockReset();
	mockUseSummaryBackfillJobs.mockReset();
	mockUseBackfillEligible.mockReset();
	mockCancelMutate.mockClear();
	mockBackfillMutate.mockClear();
	wsState.lastWsCallback = null;
	wsState.backfillCalls = [];
	wsState.cancelCalls = [];
	// Default eligibility: 4 runs so existing tests see the button. The
	// "hide when zero" case overrides this explicitly.
	mockUseBackfillEligible.mockReturnValue({
		data: {
			eligible: 4,
			estimated_cost_usd: "0.01",
			cost_basis: "fallback",
		},
		isLoading: false,
	});
	// Default: an active running job matching the agent we pass below, so the
	// component renders the progress card immediately.
	mockUseSummaryBackfillJobs.mockReturnValue({
		data: {
			items: [
				{
					id: JOB_ID,
					agent_id: "agent-1",
					status: "running",
					total: 5,
					succeeded: 0,
					failed: 0,
					estimated_cost_usd: "0.01",
					actual_cost_usd: "0.00",
				},
			],
		},
	});
	mockUseSummaryBackfillJob.mockReturnValue({
		data: {
			id: JOB_ID,
			agent_id: "agent-1",
			status: "running",
			total: 5,
			succeeded: 0,
			failed: 0,
			estimated_cost_usd: "0.01",
			actual_cost_usd: "0.00",
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
});

afterEach(() => {
	sessionStorage.clear();
});

function pushUpdate(update: Record<string, unknown>) {
	wsState.lastWsCallback?.(update);
}

describe("SummaryBackfillButton — terminal state", () => {
	it("shows the progress spinner while running", () => {
		renderWithProviders(<SummaryBackfillButton agentId="agent-1" />);
		expect(screen.getByText(/backfilling summaries/i)).toBeInTheDocument();
		expect(screen.getByTestId("summary-backfill-cancel")).toBeInTheDocument();
	});

	it("keeps the card visible with a complete header after a terminal transition", async () => {
		renderWithProviders(<SummaryBackfillButton agentId="agent-1" />);
		pushUpdate({
			type: "summary_backfill_update",
			job_id: JOB_ID,
			total: 5,
			succeeded: 5,
			failed: 0,
			status: "complete",
			actual_cost_usd: "0.15",
			estimated_cost_usd: "0.10",
			timestamp: "2026-04-23T12:00:00Z",
		});
		await waitFor(() => {
			expect(screen.getByText(/backfill complete/i)).toBeInTheDocument();
		});
		// Cancel button replaced by Dismiss, card still visible.
		expect(screen.getByTestId("summary-backfill-dismiss")).toBeInTheDocument();
		expect(
			screen.queryByTestId("summary-backfill-cancel"),
		).not.toBeInTheDocument();
	});

	it("renders the 'Review failed runs' link when failures are present", async () => {
		renderWithProviders(<SummaryBackfillButton agentId="agent-1" />);
		pushUpdate({
			type: "summary_backfill_update",
			job_id: JOB_ID,
			total: 9,
			succeeded: 8,
			failed: 1,
			status: "complete",
			actual_cost_usd: "0.17",
			estimated_cost_usd: "0.18",
			timestamp: "2026-04-23T12:00:00Z",
		});
		await waitFor(() => {
			expect(screen.getByText(/backfill complete/i)).toBeInTheDocument();
		});
		const link = screen.getByRole("link", { name: /review failed runs/i });
		expect(link).toHaveAttribute("href", "/agents/agent-1?tab=runs&summary=failed");
		expect(link).toHaveClass("text-[var(--bf-danger)]");
	});

	it("dismiss button hides the card and records the job_id in sessionStorage", async () => {
		const { user, rerender } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);
		pushUpdate({
			type: "summary_backfill_update",
			job_id: JOB_ID,
			total: 5,
			succeeded: 5,
			failed: 0,
			status: "complete",
			actual_cost_usd: "0.10",
			estimated_cost_usd: "0.10",
			timestamp: "2026-04-23T12:00:00Z",
		});
		await waitFor(() => {
			expect(screen.getByTestId("summary-backfill-dismiss")).toBeInTheDocument();
		});

		await user.click(screen.getByTestId("summary-backfill-dismiss"));

		// Card is gone (the component now renders the idle button).
		expect(
			screen.queryByTestId("summary-backfill-progress"),
		).not.toBeInTheDocument();
		expect(screen.getByTestId("summary-backfill-button")).toBeInTheDocument();

		// sessionStorage has the dismissed id.
		const raw = sessionStorage.getItem("bifrost:dismissed-backfills");
		expect(raw).not.toBeNull();
		expect(JSON.parse(raw as string)).toContain(JOB_ID);

		// A re-render with the same "active" running job should still skip it.
		rerender(<SummaryBackfillButton agentId="agent-1" />);
		expect(
			screen.queryByTestId("summary-backfill-progress"),
		).not.toBeInTheDocument();
	});

	it("cancel button invokes the cancel mutation while running", async () => {
		const { user } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);
		await user.click(screen.getByTestId("summary-backfill-cancel"));
		expect(mockCancelMutate).toHaveBeenCalledWith(
			expect.objectContaining({
				params: { path: { job_id: JOB_ID } },
			}),
			expect.any(Object),
		);
	});

	it("keeps the latest dry-run estimate and ignores stale responses", async () => {
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseSummaryBackfillJob.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);

		await user.click(
			screen.getByRole("button", { name: /resummarize runs/i }),
		);
		expect(wsState.backfillCalls).toHaveLength(1);

		await user.click(
			screen.getByRole("radio", { name: /all completed runs/i }),
		);
		expect(wsState.backfillCalls).toHaveLength(2);

		await act(async () => {
			wsState.backfillCalls[0].options.onSuccess?.({
				eligible: 3,
				estimated_cost_usd: "0.03",
				cost_basis: "history",
			});
		});
		expect(
			screen.queryByRole("button", { name: /^start$/i }),
		).not.toBeInTheDocument();

		await act(async () => {
			wsState.backfillCalls[1].options.onSuccess?.({
				eligible: 7,
				estimated_cost_usd: "0.07",
				cost_basis: "fallback",
			});
		});
		expect(
			screen.getByRole("button", { name: /^start$/i }),
		).toBeInTheDocument();
	});

	it("keeps the estimate dialog open when dry-run loading fails and retries the latest scope", async () => {
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseSummaryBackfillJob.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);

		await user.click(
			screen.getByRole("button", { name: /resummarize runs/i }),
		);
		expect(wsState.backfillCalls).toHaveLength(1);
		await act(async () => {
			wsState.backfillCalls[0].options.onError?.(new Error("boom"));
		});
		expect(
			screen.getByText(/failed to compute estimate/i),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: /retry estimate/i }),
		);
		expect(wsState.backfillCalls).toHaveLength(2);
		await act(async () => {
			wsState.backfillCalls[1].options.onSuccess?.({
				eligible: 5,
				estimated_cost_usd: "0.05",
				cost_basis: "history",
			});
		});
		expect(
			screen.getByRole("button", { name: /^start$/i }),
		).toBeInTheDocument();
	});

	it("shows a persistent inline error when start fails and clears it on retry or scope change", async () => {
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseSummaryBackfillJob.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);

		await user.click(
			screen.getByRole("button", { name: /resummarize runs/i }),
		);
		await act(async () => {
			wsState.backfillCalls[0].options.onSuccess?.({
				eligible: 4,
				estimated_cost_usd: "0.04",
				cost_basis: "history",
			});
		});
		await user.click(screen.getByRole("button", { name: /^start$/i }));
		expect(wsState.backfillCalls).toHaveLength(2);
		await act(async () => {
			wsState.backfillCalls[1].options.onError?.(new Error("boom"));
		});
		expect(
			screen.getByText(/failed to start resummarization/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /retry start/i }),
		).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /retry start/i }));
		expect(wsState.backfillCalls).toHaveLength(3);
		expect(
			screen.queryByText(/failed to start resummarization/i),
		).not.toBeInTheDocument();

		await act(async () => {
			wsState.backfillCalls[2].options.onSuccess?.({
				eligible: 4,
				estimated_cost_usd: "0.04",
				cost_basis: "history",
				job_id: JOB_ID,
				queued: 4,
			});
		});
		expect(
			screen.getByTestId("summary-backfill-progress"),
		).toBeInTheDocument();
	});

	it("clears a start failure when the scope changes and recomputes the estimate", async () => {
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseSummaryBackfillJob.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);

		await user.click(
			screen.getByRole("button", { name: /resummarize runs/i }),
		);
		await act(async () => {
			wsState.backfillCalls[0].options.onSuccess?.({
				eligible: 4,
				estimated_cost_usd: "0.04",
				cost_basis: "history",
			});
		});
		await user.click(screen.getByRole("button", { name: /^start$/i }));
		await act(async () => {
			wsState.backfillCalls[1].options.onError?.(new Error("boom"));
		});
		expect(
			screen.getByText(/failed to start resummarization/i),
		).toBeInTheDocument();

		await user.click(screen.getByRole("radio", { name: /all completed runs/i }));
		expect(
			screen.queryByText(/failed to start resummarization/i),
		).not.toBeInTheDocument();
		expect(wsState.backfillCalls).toHaveLength(3);
	});

	it("shows loading progress instead of a fake running snapshot while the job fetch is pending", () => {
		const refetch = vi.fn();
		mockUseSummaryBackfillJob.mockReturnValue({
			data: undefined,
			isLoading: true,
			isError: false,
			isFetching: true,
			refetch,
		});
		renderWithProviders(<SummaryBackfillButton agentId="agent-1" />);
		expect(screen.getByText(/loading backfill progress/i)).toBeInTheDocument();
		expect(
			screen.getByText(/checking the current job state/i),
		).toBeInTheDocument();
		expect(screen.queryByText(/^0 \/ 0$/)).not.toBeInTheDocument();
	});

	it("lets the admin retry a failed job fetch inline", async () => {
		const refetch = vi.fn();
		mockUseSummaryBackfillJob.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch,
		});
		const { user } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);
		expect(
			screen.getByText(/could not load backfill progress/i),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /retry job/i }));
		expect(refetch).toHaveBeenCalledOnce();
	});

	it("surfaces cancel failures inline and retries the same job", async () => {
		const { user } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);
		await user.click(screen.getByTestId("summary-backfill-cancel"));
		expect(wsState.cancelCalls).toHaveLength(1);
		await act(async () => {
			wsState.cancelCalls[0].options.onError?.(new Error("nope"));
		});
		expect(
			screen.getByText(/could not cancel backfill/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /retry cancel/i }),
		).toBeInTheDocument();
		await user.click(screen.getByTestId("summary-backfill-cancel"));
		expect(wsState.cancelCalls).toHaveLength(2);
	});

	it("hides entirely when nothing is eligible for backfill", () => {
		// No active job AND eligible=0 → button should not render at all.
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseBackfillEligible.mockReturnValue({
			data: {
				eligible: 0,
				estimated_cost_usd: "0.0000",
				cost_basis: "fallback",
			},
			isLoading: false,
		});
		const { container } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);
		expect(
			screen.queryByTestId("summary-backfill-button"),
		).not.toBeInTheDocument();
		expect(
			screen.queryByTestId("summary-backfill-progress"),
		).not.toBeInTheDocument();
		// Render is essentially empty.
		expect(container.firstChild).toBeNull();
	});

	it("renders nothing while the eligibility query is loading", () => {
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseBackfillEligible.mockReturnValue({
			data: undefined,
			isLoading: true,
		});
		const { container } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);
		expect(container.firstChild).toBeNull();
	});

	it("hides the button only when ALL scopes report zero eligible", () => {
		// Both pending/failed AND older-versions = 0 → button hides.
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseBackfillEligible.mockImplementation(() => ({
			data: { eligible: 0, estimated_cost_usd: "0", cost_basis: "fallback" },
			isLoading: false,
		}));
		const { container } = renderWithProviders(
			<SummaryBackfillButton agentId="agent-1" />,
		);
		expect(container.firstChild).toBeNull();
	});

	it("shows the button when only the older-versions scope has eligible runs", () => {
		// Pending=0 but older-versions=12 → button still shows so the admin
		// can roll old-version summaries forward after a prompt bump.
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseBackfillEligible.mockImplementation(
			(_agentId: string | undefined, version?: string) => ({
				data: version
					? {
							eligible: 12,
							estimated_cost_usd: "0.24",
							cost_basis: "history",
						}
					: {
							eligible: 0,
							estimated_cost_usd: "0",
							cost_basis: "fallback",
						},
				isLoading: false,
			}),
		);
		renderWithProviders(<SummaryBackfillButton agentId="agent-1" />);
		expect(
			screen.getByTestId("summary-backfill-button"),
		).toBeInTheDocument();
	});

	it("button label says 'Resummarize runs'", () => {
		mockUseSummaryBackfillJobs.mockReturnValue({ data: { items: [] } });
		mockUseBackfillEligible.mockReturnValue({
			data: { eligible: 4, estimated_cost_usd: "0.10", cost_basis: "history" },
			isLoading: false,
		});
		renderWithProviders(<SummaryBackfillButton agentId="agent-1" />);
		expect(
			screen.getByRole("button", { name: /resummarize runs/i }),
		).toBeInTheDocument();
	});

	it("shows a cancelled header when the terminal status is 'cancelled'", async () => {
		renderWithProviders(<SummaryBackfillButton agentId="agent-1" />);
		pushUpdate({
			type: "summary_backfill_update",
			job_id: JOB_ID,
			total: 5,
			succeeded: 2,
			failed: 0,
			status: "cancelled",
			actual_cost_usd: "0.04",
			estimated_cost_usd: "0.10",
			timestamp: "2026-04-23T12:00:00Z",
		});
		await waitFor(() => {
			expect(screen.getByText(/backfill cancelled/i)).toBeInTheDocument();
		});
		expect(screen.getByTestId("summary-backfill-dismiss")).toBeInTheDocument();
	});
});
