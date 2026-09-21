import { AgentRunsTab } from "./AgentRunsTab";
/**
 * Tests for AgentRunsTab.
 *
 * Mocks the agent-runs hooks at module scope. RunReviewSheet is stubbed
 * to a thin probe so we can assert that clicking a card opens it without
 * pulling in the entire shadcn Sheet machinery.
 */

import { act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockUseInfiniteAgentRuns = vi.fn();
const mockUseAgentRun = vi.fn();
const mockSetVerdict = vi.fn();
const mockClearVerdict = vi.fn();
const mockRecordedEvaluation = vi.fn();

vi.mock("@/services/agentRuns", () => ({
	useInfiniteAgentRuns: (params: unknown) => mockUseInfiniteAgentRuns(params),
	useAgentRun: (id: string | undefined) => mockUseAgentRun(id),
	useSetVerdict: () => ({ mutate: mockSetVerdict, isPending: false }),
	useClearVerdict: () => ({ mutate: mockClearVerdict, isPending: false }),
}));

vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		recordedEvaluation: (...args: unknown[]) =>
			mockRecordedEvaluation(...args),
	},
}));

// Stub the RunReviewSheet so we don't need a real Sheet portal in jsdom.
vi.mock("./RunReviewSheet", () => ({
	RunReviewSheet: ({
		open,
		run,
	}: {
		open: boolean;
		run: { id?: string } | null;
	}) =>
		open ? (
			<div data-testid="run-sheet" data-run-id={run?.id ?? ""}>
				sheet open
			</div>
		) : null,
}));

// -----------------------------------------------------------------------------
// Fixtures
// -----------------------------------------------------------------------------

function makeRun(overrides: Record<string, unknown> = {}) {
	return {
		id: "run-1",
		agent_id: "agent-1",
		agent_name: "Triage",
		trigger_type: "test",
		status: "completed",
		iterations_used: 1,
		tokens_used: 1000,
		asked: "How do I reset my password?",
		did: "Routed to Support",
		input: {},
		output: {},
		verdict: null,
		verdict_note: null,
		duration_ms: 1500,
		created_at: "2026-04-20T00:00:00Z",
		started_at: "2026-04-20T00:00:00Z",
		metadata: {},
		...overrides,
	};
}

function makeInfinitePages(
	items: ReturnType<typeof makeRun>[],
	total?: number,
) {
	return {
		data: {
			pages: [{ items, total: total ?? items.length, next_cursor: null }],
			pageParams: [0],
		},
		isLoading: false,
		hasNextPage: false,
		isFetchingNextPage: false,
		fetchNextPage: vi.fn(),
	};
}

beforeEach(() => {
	mockUseInfiniteAgentRuns.mockReturnValue(makeInfinitePages([makeRun()]));
	mockUseAgentRun.mockReturnValue({ data: undefined });
	mockSetVerdict.mockReset();
	mockClearVerdict.mockReset();
	mockRecordedEvaluation.mockReset();
	mockRecordedEvaluation.mockResolvedValue({
		evaluationId: "eval-1",
		job_id: "job-1",
	});
});

async function renderTab(agentId = "agent-1") {
	return renderWithProviders(<AgentRunsTab agentId={agentId} />);
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("AgentRunsTab — list", () => {
	it("renders run cards from the runs hook", async () => {
		await renderTab();
		expect(
			screen.getByText(/how do i reset my password/i),
		).toBeInTheDocument();
	});

	it("exposes the run collection as a named scroll region", async () => {
		await renderTab();
		const region = screen.getByRole("region", { name: /run history/i });

		expect(region).toHaveClass("agent-runs-scroll-region");
		expect(region).toContainElement(
			screen.getByText(/how do i reset my password/i),
		);
	});

	it("shows an empty message when the list is empty", async () => {
		mockUseInfiniteAgentRuns.mockReturnValue(makeInfinitePages([], 0));
		await renderTab();
		expect(
			screen.getByText(/no runs match this filter/i),
		).toBeInTheDocument();
	});

	it("renders skeletons while loading", async () => {
		mockUseInfiniteAgentRuns.mockReturnValue({
			data: undefined,
			isLoading: true,
			hasNextPage: false,
			isFetchingNextPage: false,
			fetchNextPage: vi.fn(),
		});
		const { container } = await renderTab();
		expect(
			container.querySelectorAll(".animate-pulse").length,
		).toBeGreaterThan(0);
	});
});

describe("AgentRunsTab — search", () => {
	it("passes the search query to useAgentRuns", async () => {
		const { user } = await renderTab();
		await user.type(screen.getByLabelText(/search runs/i), "acme");
		await waitFor(() => {
			expect(mockUseInfiniteAgentRuns).toHaveBeenCalledWith(
				expect.objectContaining({ q: "acme" }),
			);
		});
	});
});

describe("AgentRunsTab — verdict actions", () => {
	it("calls useSetVerdict mutate when a 👍 toggle is clicked", async () => {
		const { user } = await renderTab();
		await user.click(screen.getByRole("button", { name: /mark as good/i }));
		await waitFor(() => {
			expect(mockSetVerdict).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { run_id: "run-1" } },
					body: { verdict: "up" },
				}),
				expect.any(Object),
			);
		});
	});

	it("calls useClearVerdict mutate when toggling off the current verdict", async () => {
		mockUseInfiniteAgentRuns.mockReturnValue(
			makeInfinitePages([makeRun({ verdict: "up" })]),
		);
		const { user } = await renderTab();
		await user.click(screen.getByRole("button", { name: /mark as good/i }));
		await waitFor(() => {
			expect(mockClearVerdict).toHaveBeenCalled();
		});
	});

	it("locks review actions while saving and retries the exact failed note", async () => {
		mockUseInfiniteAgentRuns.mockReturnValue(
			makeInfinitePages([
				makeRun({ verdict: "down", verdict_note: "Previous note" }),
			]),
		);
		const { user } = await renderTab();
		const note = screen.getByRole("textbox", {
			name: "What should it have done?",
		});
		await user.clear(note);
		await user.type(note, "Check renewal terms");
		await user.tab();
		expect(mockSetVerdict).toHaveBeenCalledTimes(1);
		expect(
			screen.getByRole("button", { name: /mark as good/i }),
		).toBeDisabled();
		expect(note).toBeDisabled();
		expect(screen.getByText("Saving review…")).toHaveAttribute(
			"role",
			"status",
		);
		const callbacks = mockSetVerdict.mock.calls[0][1];
		act(() => {
			callbacks.onError();
			callbacks.onSettled();
		});
		expect(screen.getByRole("alert")).toHaveFocus();
		expect(note).toHaveValue("Check renewal terms");
		await user.click(screen.getByRole("button", { name: "Retry review" }));
		expect(mockSetVerdict).toHaveBeenCalledTimes(2);
		expect(mockSetVerdict.mock.calls[1][0].body).toEqual({
			verdict: "down",
			note: "Check renewal terms",
		});
		act(() => {
			mockSetVerdict.mock.calls[1][1].onSuccess();
			mockSetVerdict.mock.calls[1][1].onSettled();
		});
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
		expect(note).toBeEnabled();
	});

	it("renders the queue banner when there are flagged runs", async () => {
		mockUseInfiniteAgentRuns.mockReturnValue(
			makeInfinitePages([makeRun({ verdict: "down" })]),
		);
		await renderTab();
		expect(
			screen.getByText(/1 flagged run to review/i),
		).toBeInTheDocument();
	});
});

describe("AgentRunsTab — recorded evaluation", () => {
	it("queues an exact recorded evaluation from a run card and links to the result", async () => {
		const { user } = await renderTab();

		await user.click(
			screen.getByRole("button", { name: /evaluate recorded run/i }),
		);

		await waitFor(() => {
			expect(mockRecordedEvaluation).toHaveBeenCalledWith({
				agent_id: "agent-1",
				run_ids: ["run-1"],
				all_tests: true,
				applicability: "unknown",
				judge_mode: "exact",
				applicability_overrides: [],
			});
		});
		expect(
			await screen.findByRole("link", {
				name: /open recorded evaluation/i,
			}),
		).toHaveAttribute(
			"href",
			"/agents/agent-1/quality?collection=tests&recorded=eval-1",
		);
	});

	it("keeps a failed recorded evaluation visible and retryable", async () => {
		mockRecordedEvaluation
			.mockRejectedValueOnce(new Error("Queue failed"))
			.mockResolvedValueOnce({ evaluationId: "eval-2", job_id: "job-2" });
		const { user } = await renderTab();

		await user.click(
			screen.getByRole("button", { name: /evaluate recorded run/i }),
		);
		expect(
			await screen.findByText(/could not queue recorded evaluation/i),
		).toBeVisible();

		await user.click(
			screen.getByRole("button", {
				name: /retry recorded evaluation/i,
			}),
		);

		await waitFor(() => {
			expect(mockRecordedEvaluation).toHaveBeenCalledTimes(2);
		});
		expect(
			await screen.findByRole("link", {
				name: /open recorded evaluation/i,
			}),
		).toHaveAttribute(
			"href",
			"/agents/agent-1/quality?collection=tests&recorded=eval-2",
		);
	});
});

describe("AgentRunsTab — infinite scroll", () => {
	it("renders the sentinel when more pages are available", async () => {
		const fetchNextPage = vi.fn();
		mockUseInfiniteAgentRuns.mockReturnValue({
			data: {
				pages: [{ items: [makeRun()], total: 200, next_cursor: null }],
				pageParams: [0],
			},
			isLoading: false,
			hasNextPage: true,
			isFetchingNextPage: false,
			fetchNextPage,
		});
		await renderTab();
		expect(
			await screen.findByTestId("infinite-scroll-sentinel"),
		).toBeInTheDocument();
	});

	it("does not render the sentinel when there are no more pages", async () => {
		mockUseInfiniteAgentRuns.mockReturnValue(
			makeInfinitePages([makeRun()]),
		);
		await renderTab();
		expect(
			screen.queryByTestId("infinite-scroll-sentinel"),
		).not.toBeInTheDocument();
	});
});

describe("AgentRunsTab — sheet open", () => {
	it("opens the RunReviewSheet stub when a card is clicked", async () => {
		// useAgentRun resolves the detail for the opened run id; return one
		// so the sheet stub gets a non-null `run` prop with the expected id.
		mockUseAgentRun.mockReturnValue({
			data: makeRun({ id: "run-1" }),
		});
		const { user } = await renderTab();
		// The card itself is a button with the asked text as its name
		await user.click(
			screen.getByRole("button", { name: /how do i reset/i }),
		);
		const sheet = await screen.findByTestId("run-sheet");
		expect(sheet).toHaveAttribute("data-run-id", "run-1");
	});
});

it("retries an initial read failure without claiming there are no matching runs", async () => {
	const refetch = vi.fn();
	mockUseInfiniteAgentRuns.mockReturnValue({
		...makeInfinitePages([]),
		data: undefined,
		isError: true,
		refetch,
	});
	const { user } = await renderTab();
	expect(screen.getByText("Could not load runs.")).toBeVisible();
	expect(
		screen.queryByText("No runs match this filter."),
	).not.toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Retry runs" }));
	expect(refetch).toHaveBeenCalledOnce();
});

it("retains loaded runs and retries the failed next page", async () => {
	const fetchNextPage = vi.fn();
	const refetch = vi.fn();
	mockUseInfiniteAgentRuns.mockReturnValue({
		...makeInfinitePages([makeRun()]),
		isError: true,
		isFetchNextPageError: true,
		hasNextPage: true,
		fetchNextPage,
		refetch,
	});
	const { user } = await renderTab();
	expect(screen.getByText("How do I reset my password?")).toBeVisible();
	expect(screen.getByText("Could not load more runs.")).toBeVisible();
	await user.click(screen.getByRole("button", { name: "Retry runs" }));
	expect(fetchNextPage).toHaveBeenCalledOnce();
	expect(refetch).not.toHaveBeenCalled();
});
