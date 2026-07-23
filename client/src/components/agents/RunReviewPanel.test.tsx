import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import type { components } from "@/lib/v1";

const mockRegenSummary = vi.fn();
const mockAuth = vi.fn(() => ({ isPlatformAdmin: true }));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

vi.mock("@/services/agentRuns", () => ({
	useRegenerateSummary: () => ({
		mutate: mockRegenSummary,
		isPending: false,
	}),
}));

import { RunPayloads, RunReviewPanel } from "./RunReviewPanel";

type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];

const baseRun: AgentRunDetail = {
	id: "00000000-0000-0000-0000-000000000001",
	agent_id: "00000000-0000-0000-0000-000000000002",
	agent_name: "Tier-1 Triage",
	trigger_type: "test",
	summary_status: "completed",
	status: "completed",
	iterations_used: 1,
	tokens_used: 100,
	asked: "How do I reset my password?",
	did: "Routed to Support",
	input: { message: "help" },
	output: { text: "ok" },
	verdict: null,
	verdict_note: null,
	created_at: "2026-04-21T10:00:00Z",
	metadata: {},
	steps: [],
	child_run_ids: [],
	child_runs: [],
};

describe("RunReviewPanel", () => {
	it("renders the asked text", () => {
		renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getByText(/how do i reset my password/i),
		).toBeInTheDocument();
	});

	it("keeps raw run payloads out of the normal review panel", () => {
		renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(screen.queryByText("Raw input")).not.toBeInTheDocument();
		expect(screen.queryByText("Raw output")).not.toBeInTheDocument();
	});

	it("renders raw payloads only when the advanced payload component is used", async () => {
		const { user } = renderWithProviders(
			<RunPayloads input={{ ticket_id: 42 }} output={{ routed: true }} />,
		);
		expect(screen.getByText("Raw input")).toBeInTheDocument();
		expect(screen.getByText("Raw output")).toBeInTheDocument();
		const inputDisclosure = screen.getByRole("button", {
			name: /raw input/i,
		});
		expect(inputDisclosure).toHaveAttribute("aria-expanded", "false");
		await user.click(inputDisclosure);
		expect(inputDisclosure).toHaveAttribute("aria-expanded", "true");
		const variableName = screen.getByText("ticket_id:");
		expect(inputDisclosure).toHaveAttribute(
			"aria-controls",
			variableName.closest("div[id]")?.id,
		);
		expect(variableName).toBeInTheDocument();
		expect(screen.getByText("42")).toBeInTheDocument();
	});

	it("renders the agent answer when completed (answered field, falls back to did)", () => {
		// `did` is also rendered in the "What the agent did" prose section, so the
		// text appears in both places; either is fine — we just need at
		// least one match.
		renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getAllByText(/routed to support/i).length,
		).toBeGreaterThan(0);
	});

	it("uses `answered` over `did` in the answer section when both present", () => {
		renderWithProviders(
			<RunReviewPanel
				run={{
					...baseRun,
					did: "Looked up the user, then routed.",
					answered: "Sent password-reset link",
				}}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getByText("Sent password-reset link"),
		).toBeInTheDocument();
	});

	it("humanizes executor markers when older `did` prose is the answer fallback", () => {
		renderWithProviders(
			<RunReviewPanel
				run={{
					...baseRun,
					did: "Checked the ticket with [ai_ticketing_get_ticket_details].",
					answered: null,
				}}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getAllByText("Get ticket details").length,
		).toBeGreaterThan(0);
		expect(
			screen.queryByText("ai_ticketing_get_ticket_details"),
		).not.toBeInTheDocument();
	});

	it("calls onVerdict('up') when good toggle clicked", async () => {
		const onVerdict = vi.fn();
		const { user } = renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={onVerdict}
				onNote={() => {}}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /mark as good/i }));
		expect(onVerdict).toHaveBeenCalledWith("up");
	});

	it("calls onVerdict(null) when active good toggle clicked again", async () => {
		const onVerdict = vi.fn();
		const { user } = renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict="up"
				note=""
				onVerdict={onVerdict}
				onNote={() => {}}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /mark as good/i }));
		expect(onVerdict).toHaveBeenCalledWith(null);
	});

	it("calls onNote when note input changes", async () => {
		const onNote = vi.fn();
		const { user } = renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={onNote}
			/>,
		);
		const input = screen.getByPlaceholderText(/add a note/i);
		await user.type(input, "x");
		expect(onNote).toHaveBeenCalledWith("x");
	});

	it("hides verdict bar when hideVerdictBar=true", () => {
		renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
				hideVerdictBar
			/>,
		);
		expect(
			screen.queryByRole("button", { name: /mark as good/i }),
		).not.toBeInTheDocument();
	});

	it("hides verdict bar when run is not completed", () => {
		renderWithProviders(
			<RunReviewPanel
				run={{ ...baseRun, status: "failed", error: "boom" }}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.queryByRole("button", { name: /mark as good/i }),
		).not.toBeInTheDocument();
		expect(screen.getByText(/run failed/i)).toBeInTheDocument();
		expect(screen.getByText(/boom/i)).toBeInTheDocument();
	});

	it("renders did prose even when there are zero [tool] markers", () => {
		// Regression guard: the v3 LLM occasionally omits markers. Narrative
		// must still render; markers are a bonus.
		const run: AgentRunDetail = {
			...baseRun,
			did: "Looked up the ticket and routed to Tier 2 because the issue was network-related.",
			steps: [],
		};
		renderWithProviders(
			<RunReviewPanel
				run={run}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		// `did` renders in both "What the agent did" prose AND falls back into
		// "What the agent answered" when `answered` is null — both fine.
		expect(
			screen.getAllByText(/looked up the ticket and routed to tier 2/i)
				.length,
		).toBeGreaterThan(0);
		// Tool-call list should NOT render — we have prose to show instead.
		expect(
			screen.queryByText(/what the agent did · 0 actions/i),
		).not.toBeInTheDocument();
	});

	it("uses the recorded child agent name in delegation prose", () => {
		const run: AgentRunDetail = {
			...baseRun,
			did: "Asked [delegate_to_troubleshooting_agent] to collect evidence.",
			steps: [
				{
					id: "s1",
					run_id: baseRun.id,
					step_number: 1,
					type: "tool_result",
					content: {
						tool_name: "delegate_to_troubleshooting_agent",
						child_run_id: "child-1",
						result: { status: "completed" },
					},
					created_at: baseRun.created_at,
				},
			],
			child_run_ids: ["child-1"],
			child_runs: [
				{
					id: "child-1",
					agent_id: "agent-child",
					agent_name: "Endpoint Troubleshooter",
					status: "completed",
					asked: "Collect endpoint evidence",
					did: null,
					answered: null,
					duration_ms: 1000,
					created_at: baseRun.created_at,
				},
			],
		};
		renderWithProviders(
			<RunReviewPanel
				run={run}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);

		expect(screen.getAllByText("Endpoint Troubleshooter")).toHaveLength(2);
		expect(
			screen.queryByText("Troubleshooting Agent"),
		).not.toBeInTheDocument();
	});

	it("connects verified narrative references to their grouped activity item", async () => {
		const onPreview = vi.fn();
		const onActivate = vi.fn();
		const run: AgentRunDetail = {
			...baseRun,
			did: "Checked [ai_ticketing_get_ticket_details] before triage.",
			steps: [
				{
					id: "s1",
					run_id: baseRun.id,
					step_number: 1,
					type: "tool_call",
					content: {
						tool_name: "ai_ticketing_get_ticket_details",
						arguments: { ticket_id: 423068 },
					},
					created_at: baseRun.created_at,
				},
			],
		};
		const { user } = renderWithProviders(
			<RunReviewPanel
				run={run}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
				onActivityReferencePreview={onPreview}
				onActivityReferenceActivate={onActivate}
			/>,
		);

		const reference = screen.getAllByRole("link", {
			name: /show looked up ticket details in activity/i,
		})[0];
		await user.hover(reference);
		expect(onPreview).toHaveBeenLastCalledWith("s1");
		await user.click(reference);
		expect(onActivate).toHaveBeenCalledWith("s1");
	});

	it("renders a friendly action summary when prose is unavailable", () => {
		const run: AgentRunDetail = {
			...baseRun,
			did: null,
			steps: [
				{
					id: "s1",
					run_id: baseRun.id,
					step_number: 1,
					type: "tool_call",
					content: {
						tool_name: "ai_ticketing_get_ticket_details",
						arguments: { ticket_id: 423068 },
					},
					duration_ms: 120,
					created_at: baseRun.created_at,
				},
			],
		};
		renderWithProviders(
			<RunReviewPanel
				run={run}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getByText(/what the agent did · 1 action/i),
		).toBeInTheDocument();
		expect(
			screen.getByText("Looked up ticket details"),
		).toBeInTheDocument();
		expect(
			screen.queryByText("ai_ticketing_get_ticket_details"),
		).not.toBeInTheDocument();
		expect(screen.queryByText(/423068/)).not.toBeInTheDocument();
	});

	it("does not present a failed fallback action as successful", () => {
		const run: AgentRunDetail = {
			...baseRun,
			did: null,
			steps: [
				{
					id: "s1",
					run_id: baseRun.id,
					step_number: 1,
					type: "tool_call",
					content: {
						tool_name: "ai_ticketing_submit_triage",
						arguments: {},
					},
					created_at: baseRun.created_at,
				},
				{
					id: "s2",
					run_id: baseRun.id,
					step_number: 2,
					type: "tool_error",
					content: {
						tool_name: "ai_ticketing_submit_triage",
						error: "Permission denied",
					},
					created_at: baseRun.created_at,
				},
			],
		};
		const { container } = renderWithProviders(
			<RunReviewPanel
				run={run}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(screen.getByText("Could not submit triage")).toBeInTheDocument();
		expect(
			container.querySelector('[data-activity-kind="error"]'),
		).toBeInTheDocument();
	});

	it("renders zero-argument actions without empty-object clutter", () => {
		const run: AgentRunDetail = {
			...baseRun,
			did: null, // force the fallback tool-call list
			steps: [
				{
					id: "s1",
					run_id: baseRun.id,
					step_number: 1,
					type: "tool_call",
					content: { tool_name: "list_workflows", arguments: {} },
					duration_ms: null,
					created_at: baseRun.created_at,
				},
			],
		};
		renderWithProviders(
			<RunReviewPanel
				run={run}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(screen.getByText("Listed workflows")).toBeInTheDocument();
		expect(screen.queryByText("{}")).not.toBeInTheDocument();
	});

	it("keeps non-empty tool arguments out of the normal review", () => {
		const run: AgentRunDetail = {
			...baseRun,
			did: null, // force the fallback tool-call list
			steps: [
				{
					id: "s1",
					run_id: baseRun.id,
					step_number: 1,
					type: "tool_call",
					content: {
						tool_name: "send_email",
						arguments: { to: "user@x.com", subject: "Hi" },
					},
					duration_ms: 120,
					created_at: baseRun.created_at,
				},
			],
		};
		renderWithProviders(
			<RunReviewPanel
				run={run}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(screen.getByText("Sent email")).toBeInTheDocument();
		expect(screen.queryByText(/user@x.com/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/show arguments/i)).not.toBeInTheDocument();
	});

	it("renders metadata chips when metadata present", () => {
		renderWithProviders(
			<RunReviewPanel
				run={{
					...baseRun,
					metadata: { ticket_id: "4821", org: "acme" },
				}}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(screen.getByText("ticket_id")).toBeInTheDocument();
		expect(screen.getByText("4821")).toBeInTheDocument();
	});

	it("uses 'What should it have done?' placeholder when verdict is down", () => {
		renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict="down"
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getByPlaceholderText(/what should it have done/i),
		).toBeInTheDocument();
	});

	it("hides the in-panel regenerate bar when summary is completed", () => {
		renderWithProviders(
			<RunReviewPanel
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.queryByTestId("regen-summary-panel-button"),
		).not.toBeInTheDocument();
	});

	it("shows the in-panel regenerate bar when summary is pending", () => {
		renderWithProviders(
			<RunReviewPanel
				run={{
					...baseRun,
					summary_status: "pending",
					asked: "",
					did: "",
				}}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getByTestId("regen-summary-panel-button"),
		).toBeInTheDocument();
	});

	it("disables regen for non-admins but still shows it (tooltip)", () => {
		mockAuth.mockReturnValueOnce({ isPlatformAdmin: false });
		renderWithProviders(
			<RunReviewPanel
				run={{
					...baseRun,
					summary_status: "failed",
					asked: "",
					did: "",
				}}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		const btn = screen.getByTestId("regen-summary-panel-button");
		expect(btn).toBeDisabled();
	});
});
