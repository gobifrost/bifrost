import { describe, expect, it, vi, beforeEach } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";

import type { components } from "@/lib/v1";
import { renderWithProviders, screen } from "@/test-utils";

const mockUseAgentRun = vi.hoisted(() => vi.fn());

vi.mock("@/services/agentRuns", () => ({
	useAgentRun: (runId: string | undefined, options: unknown) =>
		mockUseAgentRun(runId, options),
}));

import { AdvancedTimeline, Timeline } from "./Timeline";

type AgentRunStep = components["schemas"]["AgentRunStepResponse"];
type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];
type AgentRunChild = components["schemas"]["AgentRunChildResponse"];

function NavigationStateProbe() {
	const location = useLocation();
	return (
		<pre data-testid="navigation-state">
			{JSON.stringify(location.state)}
		</pre>
	);
}

function step(
	type: string,
	content: Record<string, unknown>,
	stepNumber: number,
	overrides: Partial<AgentRunStep> = {},
): AgentRunStep {
	return {
		id: `step-${stepNumber}`,
		run_id: "00000000-0000-0000-0000-000000000001",
		step_number: stepNumber,
		type,
		content,
		duration_ms: null,
		created_at: "2026-07-15T12:00:00Z",
		...overrides,
	};
}

function childRun(overrides: Partial<AgentRunDetail> = {}): AgentRunDetail {
	return {
		id: "child-1",
		agent_id: "agent-child",
		agent_name: "Troubleshooting Specialist",
		trigger_type: "delegation",
		status: "completed",
		iterations_used: 2,
		tokens_used: 1200,
		asked: "Collect endpoint evidence",
		did: "Checked the device and found low disk space.",
		answered: "The device is online with low disk space.",
		input: {},
		output: {},
		summary_status: "completed",
		metadata: {},
		created_at: "2026-07-15T12:00:00Z",
		steps: [
			step(
				"tool_call",
				{ tool_name: "ninja_get_device_details", arguments: {} },
				1,
			),
			step(
				"tool_result",
				{
					tool_name: "ninja_get_device_details",
					result: { device: "ELIJAH-LT", status: "online" },
				},
				2,
			),
		],
		child_run_ids: [],
		child_runs: [],
		...overrides,
	};
}

function childReference(overrides: Partial<AgentRunChild> = {}): AgentRunChild {
	return {
		id: "child-1",
		agent_id: "agent-child",
		agent_name: "Troubleshooting Specialist",
		status: "completed",
		asked: "Collect endpoint evidence",
		did: "Checked the device and found low disk space.",
		answered: "The device is online with low disk space.",
		duration_ms: 12_000,
		created_at: "2026-07-15T12:00:00Z",
		...overrides,
	};
}

describe("Timeline activity view", () => {
	beforeEach(() => {
		mockUseAgentRun.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: false,
		});
	});

	it("shows a helpful empty state", () => {
		renderWithProviders(<Timeline steps={[]} />);
		expect(
			screen.getByText(/no activity to summarize/i),
		).toBeInTheDocument();
	});

	it("renders one semantic operation for a decision-call-result triplet", () => {
		const steps = [
			step("llm_request", { messages_count: 2, tools_count: 17 }, 1),
			step(
				"llm_response",
				{ tool_calls: [{ name: "ai_ticketing_get_ticket_details" }] },
				2,
			),
			step(
				"tool_call",
				{
					tool_name: "ai_ticketing_get_ticket_details",
					arguments: { ticket_id: 428950 },
				},
				3,
			),
			step(
				"tool_result",
				{
					tool_name: "ai_ticketing_get_ticket_details",
					result: {
						ticket_id: 428950,
						status: "open",
						matched: true,
					},
				},
				4,
			),
		];
		renderWithProviders(<Timeline steps={steps} />);

		expect(
			screen.getByText("Looked up ticket details"),
		).toBeInTheDocument();
		expect(
			screen.getByText("Ticket: 428950 · Status: Open · Match found"),
		).toBeInTheDocument();
		expect(screen.queryByText(/decided to call/i)).not.toBeInTheDocument();
		expect(
			screen.queryByText(/called ai_ticketing/i),
		).not.toBeInTheDocument();
		expect(screen.queryByText(/\{"ticket_id"/i)).not.toBeInTheDocument();
	});

	it("highlights a referenced action and links its workflow execution", () => {
		const { container } = renderWithProviders(
			<Timeline
				steps={[
					step(
						"tool_call",
						{
							tool_name: "ai_ticketing_get_ticket_details",
							arguments: { ticket_id: 428950 },
						},
						1,
					),
					step(
						"tool_result",
						{
							tool_name: "ai_ticketing_get_ticket_details",
							result: { ticket_id: 428950 },
							execution_id: "execution-428950",
						},
						2,
					),
				]}
				highlightedActivityId="step-1"
			/>,
		);

		expect(
			container.querySelector('[data-activity-id="step-1"]'),
		).toHaveAttribute("data-highlighted", "true");
		expect(
			screen.getByRole("link", { name: /execution/i }),
		).toHaveAttribute("href", "/history/execution-428950");
	});

	it("keeps the final answer as a readable activity event", () => {
		renderWithProviders(
			<Timeline
				steps={[
					step(
						"llm_response",
						{ content: "Triage complete.", tool_calls: [] },
						1,
					),
				]}
			/>,
		);
		expect(screen.getByText("Final response")).toBeInTheDocument();
		expect(screen.getByText("Triage complete.")).toBeInTheDocument();
	});

	it("loads and expands delegated work inline", async () => {
		mockUseAgentRun.mockImplementation((runId: string | undefined) => ({
			data: runId === "child-1" ? childRun() : undefined,
			isLoading: false,
			isError: false,
		}));
		const { user } = renderWithProviders(
			<Timeline
				steps={[
					step(
						"tool_call",
						{
							tool_name: "delegate_to_troubleshooting_agent",
							arguments: { task: "Collect endpoint evidence" },
						},
						1,
					),
					step(
						"tool_result",
						{
							tool_name: "delegate_to_troubleshooting_agent",
							result: "Evidence collected",
							child_run_id: "child-1",
						},
						2,
					),
				]}
				childRunIds={["child-1"]}
				childRuns={[childReference()]}
			/>,
		);

		expect(
			screen.getByText("Troubleshooting Specialist"),
		).toBeInTheDocument();
		expect(
			screen.getByText("Collect endpoint evidence"),
		).toBeInTheDocument();
		expect(
			screen.getByLabelText("Delegated run status: Completed"),
		).toHaveTextContent("Completed");
		const disclosure = screen.getByRole("button", {
			name: /show details for troubleshooting specialist/i,
		});
		expect(disclosure).toHaveAccessibleDescription("Completed");
		expect(disclosure).toContainElement(
			screen.getByText("Troubleshooting Specialist"),
		);
		expect(disclosure).toContainElement(
			screen.getByText("Collect endpoint evidence"),
		);
		expect(
			screen.getByRole("link", {
				name: /open troubleshooting specialist run/i,
			}),
		).toHaveAttribute("href", "/agents/agent-child/runs/child-1");
		await user.click(screen.getByText("Collect endpoint evidence"));
		const expandedDisclosure = screen.getByRole("button", {
			name: /hide details for troubleshooting specialist/i,
		});
		expect(expandedDisclosure).toHaveAttribute("aria-expanded", "true");
		expect(expandedDisclosure).toHaveAttribute(
			"aria-controls",
			expect.stringContaining("details"),
		);
		expect(
			screen.getByText("Checked the device and found low disk space."),
		).toBeInTheDocument();
		expect(
			screen.getByText("Looked up device details"),
		).toBeInTheDocument();
	});

	it("carries the parent run origin when opening delegated work", async () => {
		const { user } = renderWithProviders(
			<Routes>
				<Route
					path="/"
					element={
						<Timeline
							steps={[]}
							childRunIds={["child-1"]}
							childRuns={[childReference()]}
							childRunOrigin={{
								href: "/agents/agent-parent/runs/run-parent",
								label: "Back to Service Desk Triage run",
							}}
						/>
					}
				/>
				<Route
					path="/agents/:agentId/runs/:runId"
					element={<NavigationStateProbe />}
				/>
			</Routes>,
		);

		await user.click(
			screen.getByRole("link", {
				name: /open troubleshooting specialist run/i,
			}),
		);
		expect(screen.getByTestId("navigation-state")).toHaveTextContent(
			JSON.stringify({
				agentRunOrigin: {
					href: "/agents/agent-parent/runs/run-parent",
					label: "Back to Service Desk Triage run",
				},
			}),
		);
	});

	it("supports parent-owned delegation state and restores the source row", async () => {
		mockUseAgentRun.mockImplementation((runId: string | undefined) => ({
			data: runId === "child-1" ? childRun() : undefined,
			isLoading: false,
			isError: false,
		}));
		const scrollIntoView = vi.fn();
		Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
			configurable: true,
			value: scrollIntoView,
		});
		const onExpandedChange = vi.fn();
		const steps = [
			step(
				"tool_call",
				{
					tool_name: "delegate_to_troubleshooting_agent",
					arguments: { task: "Collect endpoint evidence" },
				},
				1,
			),
			step(
				"tool_result",
				{
					tool_name: "delegate_to_troubleshooting_agent",
					result: "Evidence collected",
					child_run_id: "child-1",
				},
				2,
			),
		];
		const timeline = (expanded: ReadonlySet<string>, restore?: string) => (
			<Timeline
				steps={steps}
				childRunIds={["child-1"]}
				childRuns={[childReference()]}
				expandedDelegationIds={expanded}
				onDelegationExpandedChange={onExpandedChange}
				restoreActivityId={restore}
			/>
		);
		const { user, rerender } = renderWithProviders(
			timeline(new Set<string>()),
		);

		await user.click(
			screen.getByRole("button", {
				name: /show details for troubleshooting specialist/i,
			}),
		);
		expect(onExpandedChange).toHaveBeenCalledWith("step-1", true);

		rerender(timeline(new Set(["step-1"]), "step-1"));
		expect(
			screen.getByRole("button", {
				name: /hide details for troubleshooting specialist/i,
			}),
		).toHaveAttribute("aria-expanded", "true");
		expect(
			screen.getByText("Checked the device and found low disk space."),
		).toBeInTheDocument();
		expect(scrollIntoView).toHaveBeenCalledWith({
			behavior: "auto",
			block: "center",
		});
	});

	it("keeps delegated status copy accurate and resilient to long content", () => {
		const agentName =
			"EndpointTroubleshootingSpecialistWithAnExtremelyLongUnbrokenName";
		renderWithProviders(
			<Timeline
				steps={[
					step(
						"tool_call",
						{
							tool_name: "delegate_to_troubleshooting_agent",
							arguments: { task: "Collect endpoint evidence" },
						},
						1,
					),
					step(
						"tool_result",
						{
							tool_name: "delegate_to_troubleshooting_agent",
							result: "Cancellation requested",
							child_run_id: "child-1",
						},
						2,
					),
				]}
				childRunIds={["child-1"]}
				childRuns={[
					childReference({
						agent_name: agentName,
						status: "cancelling",
					}),
				]}
			/>,
		);

		expect(screen.getByText(agentName)).toHaveClass(
			"min-w-0",
			"break-words",
		);
		const status = screen.getByLabelText(
			"Delegated run status: Cancelling",
		);
		expect(status).toHaveTextContent("Cancelling");
		expect(status).toHaveClass("shrink-0", "whitespace-nowrap");
		expect(
			screen.getByRole("button", {
				name: new RegExp(`show details for ${agentName}`, "i"),
			}),
		).toHaveAccessibleDescription("Cancelling");
	});

	it("lazily resolves an unlinked historical child run", async () => {
		mockUseAgentRun.mockImplementation((runId: string | undefined) => ({
			data: runId === "child-1" ? childRun() : undefined,
			isLoading: false,
			isError: false,
		}));
		const { user } = renderWithProviders(
			<Timeline
				steps={[]}
				childRunIds={["child-1"]}
				childRuns={[childReference()]}
			/>,
		);
		expect(
			screen.getByText("Troubleshooting Specialist"),
		).toBeInTheDocument();
		expect(mockUseAgentRun).toHaveBeenCalledWith(
			undefined,
			expect.any(Object),
		);
		await user.click(
			screen.getByRole("button", {
				name: /^show details for troubleshooting specialist$/i,
			}),
		);
		expect(mockUseAgentRun).toHaveBeenCalledWith(
			"child-1",
			expect.any(Object),
		);
	});

	it("refreshes an expanded delegation only while the child is active", async () => {
		mockUseAgentRun.mockImplementation((runId: string | undefined) => ({
			data:
				runId === "child-1"
					? childRun({ status: "running" })
					: undefined,
			isLoading: false,
			isError: false,
		}));
		const { user } = renderWithProviders(
			<Timeline
				steps={[]}
				childRunIds={["child-1"]}
				childRuns={[childReference({ status: "running" })]}
			/>,
		);
		await user.click(
			screen.getByRole("button", {
				name: /^show details for troubleshooting specialist$/i,
			}),
		);

		const options = mockUseAgentRun.mock.calls.at(-1)?.[1] as {
			refetchInterval: (query: {
				state: { data: AgentRunDetail | undefined };
			}) => number | false;
		};
		expect(
			options.refetchInterval({
				state: { data: childRun({ status: "running" }) },
			}),
		).toBe(2_000);
		expect(
			options.refetchInterval({
				state: { data: childRun({ status: "completed" }) },
			}),
		).toBe(false);
	});

	it("adds payload details to the same grouped actions in Advanced", async () => {
		const steps = [
			step(
				"llm_response",
				{ tool_calls: [{ name: "ai_ticketing_get_ticket_details" }] },
				1,
			),
			step(
				"tool_call",
				{
					tool_name: "ai_ticketing_get_ticket_details",
					arguments: { ticket_id: 428950 },
				},
				2,
			),
			step(
				"tool_result",
				{
					tool_name: "ai_ticketing_get_ticket_details",
					result: { ticket_id: 428950, status: "open" },
				},
				3,
			),
		];
		const { user } = renderWithProviders(
			<Timeline steps={steps} showTechnicalDetails />,
		);

		expect(
			screen.getByText("Looked up ticket details"),
		).toBeInTheDocument();
		expect(screen.queryByText(/decided to call/i)).not.toBeInTheDocument();
		expect(
			screen.queryByText(/called ai_ticketing/i),
		).not.toBeInTheDocument();

		await user.click(screen.getByText("Details", { exact: true }));
		expect(
			screen.getByText("ai_ticketing_get_ticket_details", {
				exact: true,
			}),
		).toBeInTheDocument();
		expect(screen.getAllByText("ticket_id:")).toHaveLength(2);
		expect(screen.getByText("Steps 2–3")).toBeInTheDocument();
	});
});

describe("AdvancedTimeline", () => {
	it("keeps exact executor labels and shows arguments in the variable tree", async () => {
		const { user } = renderWithProviders(
			<AdvancedTimeline
				steps={[
					step(
						"tool_call",
						{
							tool_name: "send_email",
							arguments: { to: "u@x.com", subject: "Hi" },
						},
						1,
					),
				]}
			/>,
		);
		expect(screen.getByText(/called send_email/i)).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: /toggle details for step 1/i }),
		);
		expect(screen.getByText("to:")).toBeInTheDocument();
		expect(screen.getByText('"u@x.com"')).toBeInTheDocument();
	});

	it("expands structured results and errors without coercing them to text", async () => {
		const { user } = renderWithProviders(
			<AdvancedTimeline
				steps={[
					step(
						"tool_result",
						{
							tool_name: "get_ticket",
							result: { ticket_id: 428950, status: "open" },
						},
						1,
					),
					step(
						"tool_error",
						{
							tool_name: "submit_triage",
							error: { code: "rate_limited", retry_after: 30 },
						},
						2,
					),
				]}
			/>,
		);

		expect(screen.queryByText(/\{"ticket_id"/)).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: /toggle details for step 1/i }),
		);
		expect(screen.getByText("ticket_id:")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: /toggle details for step 2/i }),
		);
		expect(screen.getByText('"rate_limited"')).toBeInTheDocument();
	});

	it("retains unknown executor events only in Advanced", () => {
		const custom = step("custom_kind", { foo: "bar" }, 1);
		const { rerender } = renderWithProviders(<Timeline steps={[custom]} />);
		expect(screen.queryByText("custom_kind")).not.toBeInTheDocument();
		rerender(<AdvancedTimeline steps={[custom]} />);
		expect(screen.getByText("custom_kind")).toBeInTheDocument();
	});
});
