import { describe, it, expect, vi, beforeEach } from "vitest";
import { Routes, Route } from "react-router-dom";
import { useLocation } from "react-router-dom";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUseAgent = vi.fn();
const mockUseAgentRuns = vi.fn();
const mockUseAgentStats = vi.fn();
const mockTuningSession = vi.fn();
const mockTuningDryRun = vi.fn();
const mockApplyTuning = vi.fn();

vi.mock("@/hooks/useAgents", () => ({
	useAgent: (id: string | undefined) => mockUseAgent(id),
}));

vi.mock("@/services/agentRuns", () => ({
	useAgentRuns: (params: unknown) => mockUseAgentRuns(params),
	useAgentRun: () => ({ data: null, isLoading: false }),
}));

vi.mock("@/services/agents", () => ({
	useAgentStats: (id: string | undefined) => mockUseAgentStats(id),
}));

vi.mock("@/services/agentTuning", () => ({
	useTuningSession: () => ({
		mutate: mockTuningSession,
		isPending: false,
	}),
	useTuningDryRun: () => ({
		mutate: mockTuningDryRun,
		isPending: false,
	}),
	useApplyTuning: () => ({ mutate: mockApplyTuning, isPending: false }),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

const baseAgent = {
	id: "agent-1",
	name: "Test Parent Agent",
	system_prompt: "You are a helpful triage agent.",
};

const baseStats = {
	agent_id: "agent-1",
	runs_7d: 47,
	success_rate: 0.92,
	avg_duration_ms: 1200,
	total_cost_7d: "0.42",
	last_run_at: "2026-04-22T00:00:00Z",
	runs_by_day: [],
	needs_review: 2,
	unreviewed: 2,
};

function makeRun(id: string) {
	return {
		id,
		agent_id: "agent-1",
		agent_name: "Triage",
		trigger_type: "manual",
		status: "completed",
		iterations_used: 1,
		tokens_used: 100,
		duration_ms: 500,
		asked: `asked-${id}`,
		did: `did-${id}`,
		input: {},
		output: {},
		verdict: "down",
		verdict_note: `note-${id}`,
		created_at: "2026-04-20T00:00:00Z",
		started_at: "2026-04-20T00:00:00Z",
		metadata: {},
	};
}

beforeEach(() => {
	mockUseAgent.mockReturnValue({ data: baseAgent });
	mockUseAgentRuns.mockReturnValue({
		data: { items: [makeRun("a"), makeRun("b")], total: 2, next_cursor: null },
		isLoading: false,
	});
	mockUseAgentStats.mockReturnValue({ data: baseStats, isLoading: false });
	mockTuningSession.mockReset();
	mockTuningDryRun.mockReset();
	mockApplyTuning.mockReset();
});

async function renderPage() {
	const { AgentTuneWorkbench } = await import("./AgentTuneWorkbench");
	return renderWithProviders(
		<Routes>
			<Route path="/agents/:id/tune" element={<AgentTuneWorkbench />} />
			<Route path="/agents/:id" element={<div>agent page</div>} />
		</Routes>,
		{ initialEntries: ["/agents/agent-1/tune"] },
	);
}

describe("AgentTuneWorkbench — shell", () => {
	it("renders the header, stat strip, and three panes", async () => {
		await renderPage();

		expect(
			screen.getByRole("heading", { name: /tune agent/i }),
		).toBeInTheDocument();
		expect(screen.getByText("Flagged runs")).toBeInTheDocument();
		expect(screen.getByText("Runs (7d)")).toBeInTheDocument();
		expect(screen.getByText("47")).toBeInTheDocument();

		expect(screen.getByTestId("tune-pane-flagged")).toBeInTheDocument();
		expect(screen.getByTestId("tune-pane-editor")).toBeInTheDocument();
	});

	it("lists flagged runs in the left pane", async () => {
		await renderPage();
		const pane = screen.getByTestId("tune-pane-flagged");
		expect(pane).toHaveTextContent("asked-a");
		expect(pane).toHaveTextContent("asked-b");
	});

	it("disables Generate proposal when there are no flagged runs", async () => {
		mockUseAgentRuns.mockReturnValue({
			data: { items: [], total: 0, next_cursor: null },
			isLoading: false,
		});
		await renderPage();
		expect(screen.getByTestId("generate-proposal-button")).toBeDisabled();
	});

	it("enables Generate proposal when there are flagged runs", async () => {
		await renderPage();
		expect(screen.getByTestId("generate-proposal-button")).toBeEnabled();
	});

	it("renders the empty-state placeholder in the editor pane when no proposal exists", async () => {
		await renderPage();
		expect(screen.getByTestId("editor-empty-state")).toBeInTheDocument();
	});

	it("renders the Run dry-run button in the header and disables it with no proposal", async () => {
		await renderPage();
		expect(screen.getByTestId("dryrun-button")).toBeDisabled();
	});
});

const sampleProposal = {
	summary: "Tighten routing rules for password resets.",
	proposed_prompt:
		"You are a helpful triage agent. Always route password resets to Support.",
	affected_run_ids: ["a", "b"],
};

describe("AgentTuneWorkbench — generate proposal", () => {
	beforeEach(() => {
		mockTuningSession.mockImplementation((_args, opts) => {
			opts?.onSuccess?.(sampleProposal);
		});
	});

	it("calls useTuningSession when the left-pane button is clicked", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("generate-proposal-button"));
		await waitFor(() => {
			expect(mockTuningSession).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { agent_id: "agent-1" } },
				}),
				expect.any(Object),
			);
		});
	});

	it("renders the editable textarea with the proposal after generate", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("generate-proposal-button"));
		const textarea = await screen.findByTestId(
			"proposal-textarea",
		);
		expect(textarea).toHaveValue(sampleProposal.proposed_prompt);
	});

	it("renders the diff viewer after a proposal is generated", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("generate-proposal-button"));
		expect(
			await screen.findByTestId("prompt-diff-viewer"),
		).toBeInTheDocument();
	});

	it("edits to the textarea update the diff viewer content", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("generate-proposal-button"));
		const textarea = (await screen.findByTestId(
			"proposal-textarea",
		)) as HTMLTextAreaElement;
		await user.clear(textarea);
		await user.type(textarea, "Brand new prompt.");
		expect(textarea).toHaveValue("Brand new prompt.");
		expect(screen.getByText(/brand new prompt\./i)).toBeInTheDocument();
	});

	it("Discard returns to the empty state", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("generate-proposal-button"));
		await screen.findByTestId("proposal-textarea");
		await user.click(screen.getByTestId("discard-button"));
		expect(screen.queryByTestId("proposal-textarea")).toBeNull();
		expect(screen.getByTestId("editor-empty-state")).toBeInTheDocument();
	});
});

const sampleDryRun = {
	results: [
		{
			run_id: "a",
			would_still_decide_same: false,
			reasoning: "Now routes to Support",
			confidence: 0.9,
		},
		{
			run_id: "b",
			would_still_decide_same: true,
			reasoning: "Still answers itself",
			confidence: 0.7,
		},
	],
};

describe("AgentTuneWorkbench — dry-run", () => {
	beforeEach(() => {
		mockTuningSession.mockImplementation((_args, opts) => {
			opts?.onSuccess?.(sampleProposal);
		});
		mockTuningDryRun.mockImplementation((_args, opts) => {
			opts?.onSuccess?.(sampleDryRun);
		});
	});

	it("enables the dry-run button once a proposal exists", async () => {
		const { user } = await renderPage();
		expect(screen.getByTestId("dryrun-button")).toBeDisabled();
		await user.click(screen.getByTestId("generate-proposal-button"));
		await screen.findByTestId("proposal-textarea");
		expect(screen.getByTestId("dryrun-button")).toBeEnabled();
	});

	it("dry-run sends the current textarea contents", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("generate-proposal-button"));
		const textarea = (await screen.findByTestId(
			"proposal-textarea",
		)) as HTMLTextAreaElement;
		await user.clear(textarea);
		await user.type(textarea, "Edited proposed prompt.");
		await user.click(screen.getByTestId("dryrun-button"));
		await waitFor(() => {
			expect(mockTuningDryRun).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { agent_id: "agent-1" } },
					body: { proposed_prompt: "Edited proposed prompt." },
				}),
				expect.any(Object),
			);
		});
	});

	it("renders per-run dry-run results after the call succeeds", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("generate-proposal-button"));
		await screen.findByTestId("proposal-textarea");
		await user.click(screen.getByTestId("dryrun-button"));
		await screen.findByTestId("dryrun-results");
		expect(screen.getByText(/1 of 2/i)).toBeInTheDocument();
		expect(screen.getByText(/now routes to support/i)).toBeInTheDocument();
		expect(screen.getByText(/still answers itself/i)).toBeInTheDocument();
	});

	it("re-running dry-run replaces the prior results", async () => {
		const { user } = await renderPage();
		await user.click(screen.getByTestId("generate-proposal-button"));
		await screen.findByTestId("proposal-textarea");
		await user.click(screen.getByTestId("dryrun-button"));
		await screen.findByTestId("dryrun-results");

		mockTuningDryRun.mockImplementation((_args, opts) => {
			opts?.onSuccess?.({
				results: [
					{
						run_id: "a",
						would_still_decide_same: false,
						reasoning: "Different outcome this time",
						confidence: 0.8,
					},
				],
			});
		});

		await user.click(screen.getByTestId("dryrun-button"));
		await waitFor(() => {
			expect(
				screen.queryByText(/still answers itself/i),
			).toBeNull();
			expect(
				screen.getByText(/different outcome this time/i),
			).toBeInTheDocument();
		});
	});
});

function LocationProbe() {
	const loc = useLocation();
	return <div data-testid="location">{loc.pathname}</div>;
}

async function renderPageWithProbe() {
	const { AgentTuneWorkbench } = await import("./AgentTuneWorkbench");
	return renderWithProviders(
		<Routes>
			<Route
				path="/agents/:id/tune"
				element={
					<>
						<AgentTuneWorkbench />
						<LocationProbe />
					</>
				}
			/>
			<Route
				path="/agents/:id"
				element={<LocationProbe />}
			/>
		</Routes>,
		{ initialEntries: ["/agents/agent-1/tune"] },
	);
}

describe("AgentTuneWorkbench — apply", () => {
	beforeEach(() => {
		mockTuningSession.mockImplementation((_args, opts) => {
			opts?.onSuccess?.(sampleProposal);
		});
		mockApplyTuning.mockImplementation((_args, opts) => {
			opts?.onSuccess?.({
				agent_id: "agent-1",
				history_id: "h-1",
				affected_run_ids: ["a", "b"],
			});
		});
	});

	it("apply sends the current textarea contents, not the original proposal", async () => {
		const { user } = await renderPageWithProbe();
		await user.click(screen.getByTestId("generate-proposal-button"));
		const textarea = (await screen.findByTestId(
			"proposal-textarea",
		)) as HTMLTextAreaElement;
		await user.clear(textarea);
		await user.type(textarea, "Hand-edited final prompt.");
		await user.click(screen.getByTestId("apply-button"));

		await waitFor(() => {
			expect(mockApplyTuning).toHaveBeenCalledWith(
				expect.objectContaining({
					params: { path: { agent_id: "agent-1" } },
					body: { new_prompt: "Hand-edited final prompt." },
				}),
				expect.any(Object),
			);
		});
	});

	it("apply navigates to /agents/:id on success", async () => {
		const { user } = await renderPageWithProbe();
		await user.click(screen.getByTestId("generate-proposal-button"));
		await screen.findByTestId("proposal-textarea");
		await user.click(screen.getByTestId("apply-button"));
		await waitFor(() => {
			expect(screen.getByTestId("location")).toHaveTextContent(
				"/agents/agent-1",
			);
		});
	});
});
