import { AgentRunDetailPage } from "./AgentRunDetailPage";
/**
 * Tests for AgentRunDetailPage.
 *
 * Mocks the run + agent + review hooks at module scope. RunReviewPanel is
 * stubbed to a thin probe — it has its own tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { Link, Routes, Route } from "react-router-dom";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { createAgentRunNavigationState } from "@/lib/agent-run-navigation";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockUseAgentRun = vi.fn();
const mockSetVerdict = vi.fn();
const mockClearVerdict = vi.fn();
const mockRegenSummary = vi.fn();
const mockRerun = vi.fn();
const mockRecordedEvaluation = vi.fn();
let rerunPending = false;

vi.mock("@/services/agentRuns", () => ({
	useAgentRun: (id: string | undefined) => mockUseAgentRun(id),
	useAgentRunStream: () => {},
	useSetVerdict: () => ({ mutate: mockSetVerdict, isPending: false }),
	useClearVerdict: () => ({ mutate: mockClearVerdict, isPending: false }),
	useRegenerateSummary: () => ({
		mutate: mockRegenSummary,
		isPending: false,
	}),
	useRerunAgentRun: () => ({
		mutate: mockRerun,
		isPending: rerunPending,
	}),
}));

const mockUseAgent = vi.fn();
vi.mock("@/hooks/useAgents", () => ({
	useAgent: (id: string | undefined) => mockUseAgent(id),
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: (query: string) => query.includes("min-width"),
}));

const mockUseExecution = vi.fn();
vi.mock("@/hooks/useExecutions", () => ({
	useExecution: (id: string | undefined) => mockUseExecution(id),
}));

vi.mock("@/hooks/useAgentRunUpdates", () => ({
	useAgentRunUpdates: () => {},
}));

vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		recordedEvaluation: (...args: unknown[]) =>
			mockRecordedEvaluation(...args),
	},
}));

const mockAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

// Stub heavy children
vi.mock("@/components/agents/RunReviewPanel", () => ({
	RunPayloads: () => (
		<div>
			<button type="button">Raw input</button>
			<button type="button">Raw output</button>
		</div>
	),
	RunReviewPanel: ({
		run,
		verdict,
		onVerdict,
		note,
		onNote,
		onActivityReferencePreview,
		onActivityReferenceActivate,
	}: {
		run: { id: string };
		note: string;
		onNote: (value: string) => void;
		verdict: string | null;
		onVerdict: (v: string | null) => void;
		onActivityReferencePreview?: (activityId: string | null) => void;
		onActivityReferenceActivate?: (activityId: string) => void;
	}) => (
		<div data-testid="run-review-panel" data-run-id={run.id}>
			<input
				aria-label="Review note"
				value={note}
				onChange={(e) => onNote(e.target.value)}
			/>
			<span data-testid="verdict-label">{verdict ?? "none"}</span>
			<button
				type="button"
				onClick={() => onVerdict("up")}
				data-testid="set-up"
			>
				up
			</button>
			<button
				type="button"
				onClick={() => onVerdict("down")}
				data-testid="set-down"
			>
				down
			</button>
			<button
				type="button"
				onClick={() => onVerdict(null)}
				data-testid="clear-verdict"
			>
				clear
			</button>
			<button
				type="button"
				onMouseEnter={() => onActivityReferencePreview?.("step-1")}
				onMouseLeave={() => onActivityReferencePreview?.(null)}
				onClick={() => onActivityReferenceActivate?.("step-1")}
			>
				activity reference
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

function makeRun(overrides: Record<string, unknown> = {}) {
	return {
		id: "run-1",
		agent_id: "agent-1",
		agent_name: "Triage",
		trigger_type: "test",
		status: "completed",
		iterations_used: 3,
		tokens_used: 5000,
		duration_ms: 1500,
		llm_model: "claude-opus-4-7",
		asked: "Reset password please",
		did: "Routed to Support",
		started_at: "2026-04-20T12:34:56Z",
		created_at: "2026-04-20T12:34:56Z",
		input: {},
		output: {},
		verdict: null,
		verdict_note: null,
		caller_email: "alice@acme.com",
		caller_name: "Alice",
		steps: [],
		child_run_ids: [],
		child_runs: [],
		ai_usage: [],
		ai_totals: null,
		...overrides,
	};
}

const baseAgent = {
	id: "agent-1",
	name: "Tier-1 Triage",
	description: "Handles tier-1 tickets",
	is_active: true,
};

beforeEach(() => {
	rerunPending = false;
	mockUseAgentRun.mockReturnValue({ data: makeRun(), isLoading: false });
	mockUseAgent.mockReturnValue({ data: baseAgent, isLoading: false });
	mockUseExecution.mockReturnValue({ data: undefined });
	mockSetVerdict.mockReset();
	mockClearVerdict.mockReset();
	mockRegenSummary.mockReset();
	mockRecordedEvaluation.mockReset();
	mockRecordedEvaluation.mockResolvedValue({
		evaluationId: "recorded-1",
		job_id: "job-1",
	});
	mockAuth.mockReturnValue({ isPlatformAdmin: false });
});

async function renderPage(path = "/agents/agent-1/runs/run-1") {
	return renderWithProviders(
		<Routes>
			<Route
				path="/agents/:agentId/runs/:runId"
				element={<AgentRunDetailPage />}
			/>
		</Routes>,
		{ initialEntries: [path] },
	);
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("AgentRunDetailPage — header + summary", () => {
	it("uses a concise agent heading while preserving the run summary", async () => {
		// The request remains in the summary; the heading identifies the agent.
		await renderPage();
		const heading = screen.getByRole("heading", {
			name: "Triage",
		});
		expect(heading).toBeVisible();
	});

	it("renders the agent name in the breadcrumb", async () => {
		await renderPage();
		const links = screen.getAllByRole("link", {
			name: /back to tier-1 triage/i,
		});
		expect(links.length).toBeGreaterThan(0);
		for (const link of links) {
			expect(link).toHaveAttribute("href", "/agents/agent-1");
		}
	});

	it("renders a semantic badge for completed runs", async () => {
		await renderPage();
		const completedBadge = screen
			.getByText("Completed")
			.closest('[data-slot="badge"]');
		expect(completedBadge).toHaveClass(
			"border-[color:var(--bf-success)]/30",
			"bg-[color:var(--bf-success)]/10",
			"text-[color:var(--bf-success)]",
		);
	});

	it("returns to the exact in-app origin from the contextual breadcrumb", async () => {
		const { user } = renderWithProviders(
			<Routes>
				<Route
					path="/parent-run"
					element={
						<div>
							<h1>Parent run restored</h1>
							<Link
								to="/agents/agent-1/runs/run-1"
								state={createAgentRunNavigationState({
									href: "/parent-run",
									label: "Back to Service Desk Triage run",
								})}
							>
								Open child run
							</Link>
						</div>
					}
				/>
				<Route
					path="/agents/:agentId/runs/:runId"
					element={<AgentRunDetailPage />}
				/>
			</Routes>,
			{ initialEntries: ["/parent-run"] },
		);

		await user.click(screen.getByRole("link", { name: "Open child run" }));
		const back = screen.getByTestId("run-context-back");
		expect(back).toHaveTextContent("Back to Service Desk Triage run");
		expect(back).toHaveAttribute("href", "/parent-run");

		await user.click(back);
		expect(
			screen.getByRole("heading", { name: "Parent run restored" }),
		).toBeInTheDocument();
	});

	it("falls back to the parent run for a directly opened delegated run", async () => {
		mockUseAgentRun.mockImplementation((id: string | undefined) => {
			if (id === "parent-run") {
				return {
					data: makeRun({
						id: "parent-run",
						agent_id: "parent-agent",
						agent_name: "Service Desk Triage",
						parent_run_id: null,
					}),
					isLoading: false,
				};
			}
			return {
				data: makeRun({
					id: "child-run",
					agent_id: "child-agent",
					agent_name: "Troubleshooting Specialist",
					parent_run_id: "parent-run",
				}),
				isLoading: false,
			};
		});

		await renderPage("/agents/child-agent/runs/child-run");
		const back = screen.getByTestId("run-context-back");
		expect(back).toHaveTextContent("Back to Service Desk Triage run");
		expect(back).toHaveAttribute(
			"href",
			"/agents/parent-agent/runs/parent-run",
		);
	});

	it("uses the run owner for a direct root-run fallback", async () => {
		mockUseAgentRun.mockImplementation((id: string | undefined) => ({
			data:
				id === "run-1"
					? makeRun({
							agent_id: "actual-agent",
							agent_name: "Actual Owner",
						})
					: undefined,
			isLoading: false,
		}));
		mockUseAgent.mockImplementation((id: string | undefined) => ({
			data: {
				...baseAgent,
				id,
				name: "Actual Owner",
			},
			isLoading: false,
		}));

		await renderPage("/agents/stale-route-agent/runs/run-1");
		expect(mockUseAgent).toHaveBeenCalledWith("actual-agent");
		expect(screen.getByTestId("run-context-back")).toHaveAttribute(
			"href",
			"/agents/actual-agent",
		);
	});

	it("renders the RunReviewPanel with the run id", async () => {
		await renderPage();
		expect(screen.getByTestId("run-review-panel")).toHaveAttribute(
			"data-run-id",
			"run-1",
		);
	});

	it("keeps grouped work units in Advanced and nests the raw executor trace", async () => {
		mockUseAgentRun.mockReturnValue({
			data: makeRun({
				steps: [
					{
						id: "step-1",
						run_id: "run-1",
						step_number: 1,
						type: "tool_result",
						content: {
							tool_name: "get_ticket",
							result: { ticket_id: 428950 },
						},
						created_at: "2026-04-20T12:35:00Z",
					},
				],
			}),
			isLoading: false,
		});
		const { user } = await renderPage();

		expect(screen.getByText("Looked up ticket")).toBeInTheDocument();
		expect(screen.getByText("Ticket: 428950")).toBeInTheDocument();
		expect(
			screen.queryByText(/result from get_ticket/i),
		).not.toBeInTheDocument();
		expect(screen.queryByText("Raw input")).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /advanced/i }));
		expect(screen.getByText("Raw input")).toBeInTheDocument();
		expect(screen.getByText(/result from get_ticket/i)).toBeVisible();
		await user.click(
			screen.getByRole("button", { name: "Toggle details for step 1" }),
		);
		expect(
			screen.getByRole("button", { name: "Toggle details for step 1" }),
		).toHaveAttribute("aria-expanded", "true");
	});

	it("switches to Activity and focuses the activity referenced by summary prose", async () => {
		const scrollIntoView = vi.fn();
		Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
			configurable: true,
			value: scrollIntoView,
		});
		mockUseAgentRun.mockReturnValue({
			data: makeRun({
				steps: [
					{
						id: "step-1",
						run_id: "run-1",
						step_number: 1,
						type: "tool_call",
						content: {
							tool_name: "get_ticket",
							arguments: { ticket_id: 428950 },
						},
						created_at: "2026-04-20T12:35:00Z",
					},
				],
			}),
			isLoading: false,
		});
		const { user, container } = await renderPage();
		const reference = screen.getByRole("button", {
			name: "activity reference",
		});
		await user.hover(reference);
		await user.click(reference);
		expect(screen.queryByTestId("run-overview")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Show overview" }),
		).toBeInTheDocument();
		const activity = container.querySelector('[data-activity-id="step-1"]');
		expect(activity).toHaveAttribute("data-highlighted", "false");
		expect(scrollIntoView).toHaveBeenCalledWith({
			behavior: "auto",
			block: "center",
		});
		expect(activity).toHaveFocus();
	});

	it("expands Activity in place when a call is selected from Overview", async () => {
		const scrollIntoView = vi.fn();
		Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
			configurable: true,
			value: scrollIntoView,
		});
		mockUseAgentRun.mockReturnValue({
			data: makeRun({
				steps: [
					{
						id: "step-1",
						run_id: "run-1",
						step_number: 1,
						type: "tool_result",
						content: {
							tool_name: "customer_summary",
							execution_id: "execution-428950",
							result: { ticket_id: 428950 },
						},
						created_at: "2026-04-20T12:35:00Z",
					},
				],
			}),
			isLoading: false,
		});
		mockUseExecution.mockReturnValue({
			data: {
				workflow_name: "Ticket details",
				result: { ticket_id: 428950 },
			},
		});

		const { user, container } = await renderPage();
		await user.click(
			screen.getByRole("button", { name: /Ticket details/ }),
		);

		expect(screen.getByTestId("run-overview")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Focus activity" }),
		).toBeInTheDocument();
		const activityFrame = container.querySelector(
			"[data-activity-inspecting]",
		);
		expect(activityFrame).toHaveAttribute(
			"data-activity-inspecting",
			"true",
		);
		await waitFor(() =>
			expect(scrollIntoView).toHaveBeenCalledWith({
				behavior: "smooth",
				block: "start",
			}),
		);

		await user.click(
			screen.getByRole("button", { name: "Close call details" }),
		);
		await waitFor(() =>
			expect(activityFrame).toHaveAttribute(
				"data-activity-inspecting",
				"false",
			),
		);
	});
});

describe("AgentRunDetailPage — loading + empty", () => {
	it("renders skeletons while loading", async () => {
		mockUseAgentRun.mockReturnValue({ data: undefined, isLoading: true });
		const { container } = await renderPage();
		expect(
			container.querySelectorAll(".animate-pulse").length,
		).toBeGreaterThan(0);
	});

	it("renders not-found when the run is missing", async () => {
		mockUseAgentRun.mockReturnValue({ data: null, isLoading: false });
		await renderPage();
		expect(screen.getByTestId("run-not-found")).toBeInTheDocument();
	});
});

describe("AgentRunDetailPage — verdict actions", () => {
	it("calls useSetVerdict when verdict is set to up", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("set-up"));
		await waitFor(() => {
			expect(mockSetVerdict).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { run_id: "run-1" } },
					body: { verdict: "up", note: "" },
				}),
				expect.any(Object),
			);
		});
	});

	it("calls useClearVerdict when verdict is cleared", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("clear-verdict"));
		await waitFor(() => {
			expect(mockClearVerdict).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { run_id: "run-1" } },
				}),
				expect.any(Object),
			);
		});
	});
});

describe("AgentRunDetailPage — sidebar metadata", () => {
	it("keeps Activity free of the old metadata sidebar and exposes run ID copy in the header", async () => {
		const { user } = await renderPage();
		expect(
			screen.getByRole("button", { name: "Copy run ID" }),
		).toBeInTheDocument();
		expect(screen.queryByText("Metadata")).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Focus activity" }),
		);
		expect(screen.queryByText("Metadata")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /advanced/i }));
		expect(screen.queryByText("Metadata")).not.toBeInTheDocument();
	});
});

describe("AgentRunDetailPage — regenerate summary", () => {
	it("hides the regenerate button for non-admins when summary is healthy", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: false });
		await renderPage();
		expect(
			screen.queryByTestId("regen-summary-button"),
		).not.toBeInTheDocument();
	});

	it("shows the regenerate button for platform admins", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		await renderPage();
		expect(screen.getByTestId("regen-summary-button")).toBeInTheDocument();
	});

	it("does not offer sidebar regeneration to non-admins after summary failure", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: false });
		mockUseAgentRun.mockReturnValue({
			data: makeRun({ summary_status: "failed" }),
			isLoading: false,
		});
		await renderPage();
		expect(
			screen.queryByTestId("regen-summary-button"),
		).not.toBeInTheDocument();
	});

	it("calls useRegenerateSummary when the button is clicked", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		const { user } = await renderPage();
		await user.click(screen.getByTestId("regen-summary-button"));
		await waitFor(() => {
			expect(mockRegenSummary).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { run_id: "run-1" } },
				}),
				expect.any(Object),
			);
		});
	});
});

describe("AgentRunDetailPage — rerun", () => {
	it("renders the rerun button in the header", async () => {
		await renderPage();
		expect(screen.getByTestId("rerun-button")).toBeInTheDocument();
	});

	it("shows the pending rerun spinner with reduced-motion-safe animation classes", async () => {
		rerunPending = true;
		await renderPage();
		const spinner = screen.getByTestId("rerun-button").querySelector("svg");
		expect(spinner).toHaveClass(
			"animate-spin",
			"motion-reduce:animate-none",
		);
	});

	it("calls useRerunAgentRun with the current run id on click", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("rerun-button"));
		await waitFor(() => {
			expect(mockRerun).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { run_id: "run-1" } },
				}),
				expect.any(Object),
			);
		});
	});
});

describe("AgentRunDetailPage — recorded evaluation", () => {
	it("queues the current run with exact recorded evaluation defaults", async () => {
		const { user } = await renderPage();

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
			"/agents/agent-1/quality?collection=tests&recorded=recorded-1",
		);
	});

	it("leaves recorded evaluation errors visible and retryable", async () => {
		mockRecordedEvaluation
			.mockRejectedValueOnce(new Error("Queue failed"))
			.mockResolvedValueOnce({
				evaluationId: "recorded-2",
				job_id: "job-2",
			});
		const { user } = await renderPage();

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
			"/agents/agent-1/quality?collection=tests&recorded=recorded-2",
		);
	});
});

describe("AgentRunDetailPage — findings", () => {
	it("does not render the Finding action when verdict is not down", async () => {
		await renderPage();
		expect(
			screen.queryByTestId("flag-conversation-card"),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Create Finding" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByText(/improvement conversation/i),
		).not.toBeInTheDocument();
	});

	it("renders the Finding action when verdict is down", async () => {
		mockUseAgentRun.mockReturnValue({
			data: makeRun({ verdict: "down" }),
			isLoading: false,
		});
		await renderPage();
		expect(
			screen.queryByTestId("flag-conversation-card"),
		).not.toBeInTheDocument();
		expect(
			screen.queryByText(/improvement conversation/i),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Create Finding" }),
		).toBeInTheDocument();
	});
});

describe("AgentRunDetailPage — AI usage card", () => {
	it("renders the AI usage card when usage data is present", async () => {
		mockUseAgentRun.mockReturnValue({
			data: makeRun({
				ai_usage: [
					{
						provider: "anthropic",
						model: "claude-opus-4-7",
						input_tokens: 1000,
						output_tokens: 500,
						cost: "0.025",
					},
				],
				ai_totals: {
					total_input_tokens: 1000,
					total_output_tokens: 500,
					total_cost: "0.025",
					total_duration_ms: 1500,
					call_count: 1,
				},
			}),
			isLoading: false,
		});
		const { user } = await renderPage();
		await user.click(screen.getByRole("button", { name: /ai usage/i }));
		expect(screen.getByTestId("ai-usage-card")).toBeInTheDocument();
		expect(screen.getByText("claude-opus-4-7")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Focus activity" }),
		);
		await user.click(screen.getByRole("button", { name: /advanced/i }));
		await user.click(screen.getByRole("button", { name: "Show overview" }));
		await user.click(screen.getByRole("button", { name: /ai usage/i }));
		expect(screen.getByTestId("ai-usage-card")).toBeInTheDocument();
	});

	it("hides the AI usage card when there is no usage data", async () => {
		mockUseAgentRun.mockReturnValue({
			data: makeRun({
				ai_usage: [],
				ai_totals: null,
				llm_model: null,
				tokens_used: 0,
			}),
			isLoading: false,
		});
		await renderPage();
		expect(screen.queryByTestId("ai-usage-card")).not.toBeInTheDocument();
	});

	it("renders reported model and tokens for legacy runs without usage breakdown", async () => {
		mockUseAgentRun.mockReturnValue({
			data: makeRun({
				ai_usage: [],
				ai_totals: null,
				llm_model: "gpt-5.2",
				tokens_used: 5000,
			}),
			isLoading: false,
		});
		const { user } = await renderPage();
		expect(screen.getByText("AI Usage")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /ai usage/i }));
		expect(screen.getByTestId("ai-usage-card")).toBeInTheDocument();
		expect(screen.getByText("gpt-5.2")).toBeInTheDocument();
		expect(screen.getByText("5,000")).toBeInTheDocument();
	});
});

it("distinguishes failed run reads from missing runs and offers retry", async () => {
	const refetch = vi.fn();
	mockUseAgentRun.mockReturnValue({
		isLoading: false,
		isError: true,
		isFetching: false,
		refetch,
	});
	const { user } = await renderPage();
	expect(screen.queryByTestId("run-not-found")).not.toBeInTheDocument();
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Could not load run details",
	);
	await user.click(screen.getByRole("button", { name: "Retry run details" }));
	expect(refetch).toHaveBeenCalledOnce();
});
it("retains the execution when refreshing its run fails", async () => {
	mockUseAgentRun.mockReturnValue({
		data: makeRun(),
		isLoading: false,
		isError: true,
		isFetching: false,
		refetch: vi.fn(),
	});
	await renderPage();
	expect(screen.getByTestId("agent-run-detail-page")).toBeInTheDocument();
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Previously loaded data is still shown",
	);
});

it.each(["run-1", "__proto__", "constructor"])("loads and edits the stored note for run key %s", async (runId) => {
	mockUseAgentRun.mockReturnValue({
		data: makeRun({ id: runId, verdict_note: "Stored review", verdict: "down" }),
		isLoading: false,
	});
	const { user } = await renderPage(`/agents/agent-1/runs/${runId}`);
	const input = screen.getByRole("textbox", { name: "Review note" });
	expect(input).toHaveValue("Stored review");
	await user.clear(input);
	await user.type(input, "Check renewal");
	await user.click(screen.getByRole("button", { name: "Save review note" }));
	expect(mockSetVerdict).toHaveBeenCalledWith(
		expect.objectContaining({
			body: { verdict: "down", note: "Check renewal" },
		}),
		expect.any(Object),
	);
	expect(input).toBeDisabled();
});

it("retains the run and retries failed agent information", async () => {
	const refetch = vi.fn();
	mockUseAgent.mockReturnValue({ isError: true, isFetching: false, refetch });
	const { user } = await renderPage();
	expect(screen.getByTestId("agent-run-detail-page")).toBeInTheDocument();
	await user.click(
		screen.getByRole("button", { name: "Retry agent information" }),
	);
	expect(refetch).toHaveBeenCalledOnce();
});
it("keeps a child execution visible when its parent read fails", async () => {
	const refetch = vi.fn();
	mockUseAgentRun.mockImplementation((id) =>
		id === "parent-run"
			? { isError: true, isFetching: false, refetch }
			: {
					data: makeRun({ parent_run_id: "parent-run" }),
					isLoading: false,
				},
	);
	const { user } = await renderPage();
	expect(screen.getByTestId("agent-run-detail-page")).toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Retry parent run" }));
	expect(refetch).toHaveBeenCalledOnce();
});

it("keeps deleted-agent runs readable without invalid agent links", async () => {
	mockUseAgentRun.mockReturnValue({
		data: makeRun({ agent_id: null, agent_name: "Archived agent" }),
		isLoading: false,
	});
	mockUseAgent.mockReturnValue({ data: undefined, isLoading: false });
	await renderPage();
	expect(
		screen.getByText("This agent is no longer available."),
	).toBeVisible();
	expect(
		screen.getByRole("link", { name: "Back to agents" }),
	).toHaveAttribute("href", "/agents");
	expect(screen.getByRole("button", { name: "Rerun" })).toBeDisabled();
	expect(
		screen
			.getAllByRole("link")
			.some((link) => link.getAttribute("href")?.includes("/null")),
	).toBe(false);
});
