/**
 * Tests for AgentRunDetailPage.
 *
 * Mocks the run + agent + tuning hooks at module scope. RunReviewPanel and
 * FlagConversation are stubbed to thin probes — they have their own tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { Link, Routes, Route } from "react-router-dom";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { createAgentRunNavigationState } from "@/lib/agent-run-navigation";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockUseAgentRun = vi.fn();
const mockUseFlagConversation = vi.fn();
const mockSendFlagMessage = vi.fn();
const mockSetVerdict = vi.fn();
const mockClearVerdict = vi.fn();
const mockRegenSummary = vi.fn();
const mockRerun = vi.fn();

vi.mock("@/services/agentRuns", () => ({
	useAgentRun: (id: string | undefined) => mockUseAgentRun(id),
	useFlagConversation: (id: string | undefined) =>
		mockUseFlagConversation(id),
	useSendFlagMessage: () => ({
		mutate: mockSendFlagMessage,
		isPending: false,
	}),
	useSetVerdict: () => ({ mutate: mockSetVerdict, isPending: false }),
	useClearVerdict: () => ({ mutate: mockClearVerdict, isPending: false }),
	useRegenerateSummary: () => ({
		mutate: mockRegenSummary,
		isPending: false,
	}),
	useRerunAgentRun: () => ({
		mutate: mockRerun,
		isPending: false,
	}),
}));

const mockUseAgent = vi.fn();
vi.mock("@/hooks/useAgents", () => ({
	useAgent: (id: string | undefined) => mockUseAgent(id),
}));

vi.mock("@/hooks/useAgentRunUpdates", () => ({
	useAgentRunUpdates: () => {},
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
		onActivityReferencePreview,
		onActivityReferenceActivate,
	}: {
		run: { id: string };
		verdict: string | null;
		onVerdict: (v: string | null) => void;
		onActivityReferencePreview?: (activityId: string | null) => void;
		onActivityReferenceActivate?: (activityId: string) => void;
	}) => (
		<div data-testid="run-review-panel" data-run-id={run.id}>
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

vi.mock("@/components/agents/FlagConversation", () => ({
	FlagConversation: ({
		conversation,
	}: {
		conversation: { id: string } | null;
	}) => (
		<div data-testid="flag-conversation">
			conv-{conversation?.id ?? "none"}
		</div>
	),
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
	mockUseAgentRun.mockReturnValue({ data: makeRun(), isLoading: false });
	mockUseAgent.mockReturnValue({ data: baseAgent, isLoading: false });
	mockUseFlagConversation.mockReturnValue({ data: undefined });
	mockSendFlagMessage.mockReset();
	mockSetVerdict.mockReset();
	mockClearVerdict.mockReset();
	mockRegenSummary.mockReset();
	mockAuth.mockReturnValue({ isPlatformAdmin: false });
});

async function renderPage(path = "/agents/agent-1/runs/run-1") {
	const { AgentRunDetailPage } = await import("./AgentRunDetailPage");
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
	it("renders the run summary in the header", async () => {
		// Header uses `asked` as the TL;DR title (not `did` — that's prose
		// under v3+ and too long for a heading).
		await renderPage();
		expect(
			screen.getByRole("heading", { name: /reset password please/i }),
		).toBeInTheDocument();
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

	it("returns to the exact in-app origin from the contextual breadcrumb", async () => {
		const { AgentRunDetailPage } = await import("./AgentRunDetailPage");
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

		await user.click(
			screen.getByRole("link", { name: "Open child run" }),
		);
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
		expect(screen.getByText(/result from get_ticket/i)).not.toBeVisible();
		expect(screen.getByText("Looked up ticket")).toBeInTheDocument();
		expect(screen.getByText("Raw input")).toBeInTheDocument();
		await user.click(screen.getByText("Details", { exact: true }));
		expect(
			screen.getByText("get_ticket", { exact: true }),
		).toBeInTheDocument();
		expect(screen.getByText("ticket_id:")).toBeInTheDocument();
		expect(screen.getByText("428950")).toBeInTheDocument();

		await user.click(
			screen.getByText("Raw executor trace", { exact: true }),
		);
		expect(screen.getByText(/result from get_ticket/i)).toBeInTheDocument();
	});

	it("previews and scrolls to the activity referenced by summary prose", async () => {
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
		const activity = container.querySelector('[data-activity-id="step-1"]');
		expect(activity).toHaveAttribute("data-highlighted", "false");

		await user.hover(reference);
		expect(activity).toHaveAttribute("data-highlighted", "true");
		await user.unhover(reference);
		expect(activity).toHaveAttribute("data-highlighted", "false");

		await user.click(reference);
		expect(scrollIntoView).toHaveBeenCalledWith({
			behavior: "smooth",
			block: "center",
		});
		await user.unhover(reference);
		expect(activity).toHaveAttribute("data-highlighted", "false");
		expect(activity).toHaveFocus();
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
					body: { verdict: "up" },
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
	it("keeps technical metadata in Advanced while retaining operational context", async () => {
		const { user } = await renderPage();
		expect(screen.queryByText(/run-1/)).not.toBeInTheDocument();
		expect(screen.queryByText(/claude-opus-4-7/)).not.toBeInTheDocument();
		expect(screen.getByText(/test/)).toBeInTheDocument();
		expect(screen.getByText(/alice/i)).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /advanced/i }));
		expect(screen.getByText(/run-1/)).toBeInTheDocument();
		expect(screen.getByText(/claude-opus-4-7/)).toBeInTheDocument();
		expect(screen.getByText("Iterations")).toBeInTheDocument();
		expect(screen.getByText("Tokens")).toBeInTheDocument();
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

	it("shows the regenerate button when summary_status is failed (any role)", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: false });
		mockUseAgentRun.mockReturnValue({
			data: makeRun({ summary_status: "failed" }),
			isLoading: false,
		});
		await renderPage();
		expect(screen.getByTestId("regen-summary-button")).toBeInTheDocument();
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

describe("AgentRunDetailPage — flag conversation", () => {
	it("does not render the flag conversation when verdict is not down", async () => {
		await renderPage();
		expect(
			screen.queryByTestId("flag-conversation-card"),
		).not.toBeInTheDocument();
	});

	it("renders the flag conversation when verdict is down", async () => {
		mockUseAgentRun.mockReturnValue({
			data: makeRun({ verdict: "down" }),
			isLoading: false,
		});
		mockUseFlagConversation.mockReturnValue({
			data: { id: "conv-1", run_id: "run-1", messages: [] },
		});
		await renderPage();
		expect(
			screen.getByTestId("flag-conversation-card"),
		).toBeInTheDocument();
		expect(screen.getByTestId("flag-conversation")).toHaveTextContent(
			"conv-conv-1",
		);
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
		expect(screen.queryByTestId("ai-usage-card")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /advanced/i }));
		expect(screen.getByTestId("ai-usage-card")).toBeInTheDocument();
	});

	it("hides the AI usage card when there is no usage data", async () => {
		await renderPage();
		expect(screen.queryByTestId("ai-usage-card")).not.toBeInTheDocument();
	});
});
