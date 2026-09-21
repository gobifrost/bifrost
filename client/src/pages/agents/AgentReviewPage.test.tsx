import { AgentReviewPage } from "./AgentReviewPage";
/**
 * Tests for AgentReviewPage (review flipbook).
 *
 * Mocks the run-list/run-detail/verdict hooks at module scope. RunReviewPanel
 * is stubbed to a thin probe — it has its own tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { Routes, Route, useLocation } from "react-router-dom";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockUseAgentRuns = vi.fn();
const mockUseAgentRun = vi.fn();
const mockSetVerdict = vi.fn();
const mockClearVerdict = vi.fn();

vi.mock("@/services/agentRuns", () => ({
	useInfiniteAgentRuns: (params: unknown) => {
		const result = mockUseAgentRuns(params);
		return {
			...result,
			data: result.data ? { pages: [result.data] } : undefined,
		};
	},
	useAgentRun: (id: string | undefined) => mockUseAgentRun(id),
	useSetVerdict: () => ({ mutate: mockSetVerdict, isPending: false }),
	useClearVerdict: () => ({ mutate: mockClearVerdict, isPending: false }),
}));

const mockUseAgent = vi.fn();
vi.mock("@/hooks/useAgents", () => ({
	useAgent: (id: string | undefined) => mockUseAgent(id),
}));

vi.mock("@/components/agents/RunReviewPanel", () => ({
	RunReviewPanel: ({
		run,
		verdict,
		onVerdict,
		note,
		onNote,
	}: {
		run: { id: string };
		note: string;
		onNote: (value: string) => void;
		verdict: string | null;
		onVerdict: (v: string | null) => void;
	}) => (
		<div data-testid="run-review-panel" data-run-id={run.id}>
			{verdict ?? "none"}
			<input
				aria-label="Review note"
				value={note}
				onChange={(event) => onNote(event.target.value)}
			/>
			<button
				type="button"
				onClick={() => onVerdict("up")}
				data-testid="panel-up"
			>
				up
			</button>
			<button
				type="button"
				onClick={() => onVerdict("down")}
				data-testid="panel-down"
			>
				down
			</button>
		</div>
	),
}));

vi.mock("@/components/agents/RunFindingAction", () => ({
	RunFindingAction: () => <button type="button">Create Finding</button>,
}));

// -----------------------------------------------------------------------------
// Fixtures
// -----------------------------------------------------------------------------

function makeRun(id: string, overrides: Record<string, unknown> = {}) {
	return {
		id,
		agent_id: "agent-1",
		agent_name: "Triage",
		trigger_type: "test",
		status: "completed",
		iterations_used: 2,
		tokens_used: 1000,
		duration_ms: 1000,
		asked: `asked-${id}`,
		did: `did-${id}`,
		input: {},
		output: {},
		verdict: "down",
		verdict_note: "needs work",
		created_at: "2026-04-20T00:00:00Z",
		started_at: "2026-04-20T00:00:00Z",
		metadata: {},
		steps: [],
		ai_usage: [],
		ai_totals: null,
		...overrides,
	};
}

const baseAgent = { id: "agent-1", name: "Tier-1 Triage" };

beforeEach(() => {
	mockUseAgentRuns.mockReturnValue({
		data: {
			items: [makeRun("a"), makeRun("b"), makeRun("c")],
			total: 3,
			next_cursor: null,
		},
		isLoading: false,
	});
	mockUseAgentRun.mockImplementation((runId: string | undefined) => ({
		data: runId ? makeRun(runId) : undefined,
	}));
	mockUseAgent.mockReturnValue({ data: baseAgent });
	mockSetVerdict.mockReset();
	mockClearVerdict.mockReset();
	// Default: invoke success callback so tests can observe auto-advance.
	mockSetVerdict.mockImplementation((_args, opts) => {
		opts?.onSuccess?.();
		opts?.onSettled?.();
	});
	mockClearVerdict.mockImplementation((_args, opts) => {
		opts?.onSuccess?.();
		opts?.onSettled?.();
	});
});

async function renderPage(path = "/agents/agent-1/review") {
	function LocationProbe() {
		const loc = useLocation();
		return (
			<div
				data-testid="location"
				data-navigation-state={JSON.stringify(loc.state)}
			>
				{loc.pathname}
			</div>
		);
	}
	return renderWithProviders(
		<Routes>
			<Route
				path="/agents/:id/review"
				element={
					<>
						<AgentReviewPage />
						<LocationProbe />
					</>
				}
			/>
			<Route path="/agents/:id" element={<LocationProbe />} />
			<Route path="/agents/:id/runs/:runId" element={<LocationProbe />} />
		</Routes>,
		{ initialEntries: [path] },
	);
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("AgentReviewPage — basic render", () => {
	it("renders the queue counter", async () => {
		await renderPage();
		expect(screen.getByTestId("review-counter")).toHaveTextContent(
			"1 of 3",
		);
	});

	it("renders one progress dot per run in the queue", async () => {
		await renderPage();
		const dots = screen
			.getByTestId("progress-dots")
			.querySelectorAll("button");
		expect(dots.length).toBe(3);
	});

	it("renders the empty state when no runs are flagged", async () => {
		mockUseAgentRuns.mockReturnValue({
			data: { items: [], total: 0, next_cursor: null },
			isLoading: false,
		});
		await renderPage();
		expect(screen.getByTestId("review-empty")).toBeInTheDocument();
	});
});

describe("AgentReviewPage — navigation", () => {
	it("keeps the review queue as the origin for full run details", async () => {
		const { user } = await renderPage(
			"/agents/agent-1/review?filter=flagged",
		);

		await user.click(screen.getByTestId("open-detail"));
		expect(screen.getByTestId("location")).toHaveTextContent(
			"/agents/agent-1/runs/a",
		);
		expect(screen.getByTestId("location")).toHaveAttribute(
			"data-navigation-state",
			JSON.stringify({
				agentRunOrigin: {
					href: "/agents/agent-1/review?filter=flagged",
					label: "Back to Tier-1 Triage review queue",
				},
			}),
		);
	});

	it("right arrow advances to the next run", async () => {
		await renderPage();
		expect(screen.getByTestId("review-counter")).toHaveTextContent(
			"1 of 3",
		);
		fireEvent.keyDown(window, { key: "ArrowRight" });
		await waitFor(() => {
			expect(screen.getByTestId("review-counter")).toHaveTextContent(
				"2 of 3",
			);
		});
	});

	it("left arrow goes back", async () => {
		await renderPage();
		fireEvent.keyDown(window, { key: "ArrowRight" });
		await waitFor(() => {
			expect(screen.getByTestId("review-counter")).toHaveTextContent(
				"2 of 3",
			);
		});
		fireEvent.keyDown(window, { key: "ArrowLeft" });
		await waitFor(() => {
			expect(screen.getByTestId("review-counter")).toHaveTextContent(
				"1 of 3",
			);
		});
	});

	it("Next button advances", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("next-button"));
		await waitFor(() => {
			expect(screen.getByTestId("review-counter")).toHaveTextContent(
				"2 of 3",
			);
		});
	});

	it("Prev button is disabled on the first run", async () => {
		await renderPage();
		expect(screen.getByTestId("prev-button")).toBeDisabled();
	});
});

describe("AgentReviewPage — verdict actions", () => {
	it("uses the shared Finding action for negative runs", async () => {
		await renderPage();

		expect(
			screen.getByRole("button", { name: "Create Finding" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", {
				name: ["Record Finding", "from This Run"].join(" "),
			}),
		).not.toBeInTheDocument();
	});

	it("does not show the Finding action for good runs", async () => {
		mockUseAgentRun.mockImplementation((runId: string | undefined) =>
			runId ? { data: makeRun(runId, { verdict: "up" }) } : {},
		);
		await renderPage();
		expect(
			screen.queryByRole("button", { name: "Create Finding" }),
		).not.toBeInTheDocument();
	});

	it("does not show the Finding action for unreviewed runs", async () => {
		mockUseAgentRun.mockImplementation((runId: string | undefined) =>
			runId ? { data: makeRun(runId, { verdict: null }) } : {},
		);
		await renderPage();
		expect(
			screen.queryByRole("button", { name: "Create Finding" }),
		).not.toBeInTheDocument();
	});

	it("calls useSetVerdict and auto-advances on success", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("panel-up"));
		await waitFor(() => {
			expect(mockSetVerdict).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { run_id: "a" } },
					body: { verdict: "up", note: "needs work" },
				}),
				expect.any(Object),
			);
		});
		// Auto-advance
		await waitFor(() => {
			expect(screen.getByTestId("review-counter")).toHaveTextContent(
				"2 of 3",
			);
		});
	});
});

describe("AgentReviewPage — recovery and keyboard ownership", () => {
	it("shows a failed queue read instead of a successful empty state and retries", async () => {
		const refetch = vi.fn();
		mockUseAgentRuns.mockReturnValue({
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch,
		});
		const { user } = await renderPage();
		expect(screen.queryByTestId("review-empty")).not.toBeInTheDocument();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Could not load review queue",
		);
		await user.click(
			screen.getByRole("button", { name: "Retry review queue" }),
		);
		expect(refetch).toHaveBeenCalledOnce();
	});
	it("keeps cached runs visible when refreshing the queue fails", async () => {
		mockUseAgentRuns.mockReturnValue({
			data: { items: [makeRun("a")] },
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Previously loaded data is still shown",
		);
		expect(screen.getByTestId("run-review-panel")).toBeInTheDocument();
	});
	it("retries a failed detail read while preserving queue navigation", async () => {
		const refetch = vi.fn();
		mockUseAgentRun.mockReturnValue({
			isError: true,
			isFetching: false,
			refetch,
		});
		const { user } = await renderPage();
		expect(
			screen.queryByTestId("run-review-panel"),
		).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Retry run details" }),
		);
		expect(refetch).toHaveBeenCalledOnce();
		expect(screen.getByTestId("next-button")).toBeEnabled();
	});
	it("does not intercept keys owned by controls or modifier shortcuts", async () => {
		await renderPage();
		fireEvent.keyDown(screen.getByTestId("next-button"), {
			key: "ArrowRight",
		});
		fireEvent.keyDown(window, { key: "u", ctrlKey: true });
		expect(screen.getByTestId("review-counter")).toHaveTextContent(
			"1 of 3",
		);
		expect(mockSetVerdict).not.toHaveBeenCalled();
	});
});

describe("AgentReviewPage — durable review edits", () => {
	it("preserves a note while navigating and includes it in the saved verdict", async () => {
		const { user } = await renderPage();
		await user.clear(screen.getByRole("textbox", { name: "Review note" }));
		await user.type(
			screen.getByRole("textbox", { name: "Review note" }),
			"Check renewal date",
		);
		await user.click(screen.getByTestId("next-button"));
		await user.click(screen.getByTestId("prev-button"));
		expect(
			screen.getByRole("textbox", { name: "Review note" }),
		).toHaveValue("Check renewal date");
		await user.click(
			screen.getByRole("button", { name: "Save note and continue" }),
		);
		expect(mockSetVerdict).toHaveBeenCalledWith(
			expect.objectContaining({
				body: { verdict: "down", note: "Check renewal date" },
			}),
			expect.any(Object),
		);
	});
	it("prevents duplicate saves and navigation, and retains the current run after failure", async () => {
		let callbacks: { onError: () => void; onSettled: () => void };
		mockSetVerdict.mockImplementation((_args, options) => {
			callbacks = options;
		});
		const { user } = await renderPage();
		await user.click(screen.getByTestId("panel-up"));
		expect(screen.getByTestId("next-button")).toBeDisabled();
		expect(
			screen.getByRole("textbox", { name: "Review note" }),
		).toBeDisabled();
		fireEvent.keyDown(window, { key: "u" });
		expect(mockSetVerdict).toHaveBeenCalledOnce();
		const { act } = await import("@testing-library/react");
		act(() => {
			callbacks.onError();
			callbacks.onSettled();
		});
		expect(screen.getByTestId("review-counter")).toHaveTextContent(
			"1 of 3",
		);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Could not save your review",
		);
		await user.click(screen.getByRole("button", { name: "Retry review" }));
		expect(mockSetVerdict).toHaveBeenCalledTimes(2);
	});
	it("keeps the next run selected after the reviewed item leaves the queue", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("panel-up"));
		expect(screen.getByTestId("run-review-panel")).toHaveAttribute(
			"data-run-id",
			"b",
		);
		mockUseAgentRuns.mockReturnValue({
			data: { items: [makeRun("b"), makeRun("c")] },
			isLoading: false,
		});
		// Trigger a local rerender against the refreshed hook response.
		fireEvent.keyDown(window, { key: "x" });
		const note = screen.getByRole("textbox", { name: "Review note" });
		await user.type(note, " refreshed");
		expect(screen.getByTestId("run-review-panel")).toHaveAttribute(
			"data-run-id",
			"b",
		);
	});
});

describe("AgentReviewPage — queue pagination", () => {
	it("shows the full total and loads additional flagged runs", async () => {
		const fetchNextPage = vi.fn();
		mockUseAgentRuns.mockReturnValue({
			data: { items: [makeRun("a")], total: 51 },
			hasNextPage: true,
			isLoading: false,
			fetchNextPage,
		});
		const { user } = await renderPage();
		expect(screen.getByTestId("review-counter")).toHaveTextContent(
			"1 of 51",
		);
		await user.click(
			screen.getByRole("button", { name: "Load more flagged runs" }),
		);
		expect(fetchNextPage).toHaveBeenCalledOnce();
	});
	it("retains loaded reviews and retries a failed additional page", async () => {
		const fetchNextPage = vi.fn();
		mockUseAgentRuns.mockReturnValue({
			data: { items: [makeRun("a")], total: 51 },
			hasNextPage: true,
			isLoading: false,
			isError: true,
			isFetchNextPageError: true,
			fetchNextPage,
		});
		const { user } = await renderPage();
		expect(screen.getByTestId("run-review-panel")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Retry loading more runs" }),
		);
		expect(fetchNextPage).toHaveBeenCalledOnce();
		expect(
			screen.queryByRole("button", { name: "Retry review queue" }),
		).not.toBeInTheDocument();
	});
});

it("keeps the review available and retries failed agent information", async () => {
	const refetch = vi.fn();
	mockUseAgent.mockReturnValue({ isError: true, isFetching: false, refetch });
	const { user } = await renderPage();
	expect(screen.getByTestId("run-review-panel")).toBeInTheDocument();
	await user.click(
		screen.getByRole("button", { name: "Retry agent information" }),
	);
	expect(refetch).toHaveBeenCalledOnce();
});
