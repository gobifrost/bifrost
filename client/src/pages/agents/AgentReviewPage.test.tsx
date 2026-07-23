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
	useAgentRuns: (params: unknown) => mockUseAgentRuns(params),
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
	}: {
		run: { id: string };
		verdict: string | null;
		onVerdict: (v: string | null) => void;
	}) => (
		<div data-testid="run-review-panel" data-run-id={run.id}>
			{verdict ?? "none"}
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

// -----------------------------------------------------------------------------
// Fixtures
// -----------------------------------------------------------------------------

function makeRun(
	id: string,
	overrides: Record<string, unknown> = {},
) {
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
	});
	mockClearVerdict.mockImplementation((_args, opts) => {
		opts?.onSuccess?.();
	});
});

async function renderPage(path = "/agents/agent-1/review") {
	const { AgentReviewPage } = await import("./AgentReviewPage");
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
			<Route
				path="/agents/:id"
				element={<LocationProbe />}
			/>
			<Route
				path="/agents/:id/runs/:runId"
				element={<LocationProbe />}
			/>
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
	it("calls useSetVerdict and auto-advances on success", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("panel-up"));
		await waitFor(() => {
			expect(mockSetVerdict).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { run_id: "a" } },
					body: { verdict: "up" },
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
