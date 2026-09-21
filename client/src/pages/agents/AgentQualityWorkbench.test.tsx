import { describe, it, expect, vi, beforeEach } from "vitest";
import { Routes, Route } from "react-router-dom";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

import { AgentQualityWorkbench } from "./AgentQualityWorkbench";

const mockUseAgent = vi.fn();
const mockUseAgentRuns = vi.fn();
const mockFindings = vi.fn();
const mockReviews = vi.fn();
const mockAgentTests = vi.fn();
const mockLatestAgentTests = vi.fn();
const mockCreateAgentTest = vi.fn();
const mockRunAgentTests = vi.fn();
const mockRecordedEvaluationResults = vi.fn();
const mockRecordedEvaluationUsage = vi.fn();

vi.mock("@/hooks/useAgents", () => ({
	useAgent: (id: string | undefined) => mockUseAgent(id),
	useAgents: () => ({ data: [], isPending: false }),
}));

vi.mock("@/hooks/useTools", () => ({
	useToolsGrouped: () => ({
		data: { workflow: [], system: [] },
		isPending: false,
	}),
	useSystemTools: () => ({ data: { tools: [] }, isPending: false }),
}));

vi.mock("@/services/aiModels", () => ({
	listModelProfiles: vi.fn().mockResolvedValue([
		{ id: "profile-1", name: "Careful reviewer" },
	]),
}));

vi.mock("@/services/agentRuns", () => ({
	useInfiniteAgentRuns: (params: unknown) => {
		const result = mockUseAgentRuns(params);
		return {
			...result,
			data: result.data ? { pages: [result.data] } : undefined,
		};
	},
	useAgentRun: () => ({ data: null, isLoading: false }),
}));

vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		findings: (...args: unknown[]) => mockFindings(...args),
		updateFinding: vi.fn(),
		reviews: (...args: unknown[]) => mockReviews(...args),
		agentTests: (...args: unknown[]) => mockAgentTests(...args),
		latestAgentTests: (...args: unknown[]) => mockLatestAgentTests(...args),
		createAgentTest: (...args: unknown[]) => mockCreateAgentTest(...args),
		runAgentTests: (...args: unknown[]) => mockRunAgentTests(...args),
		recordedEvaluationResults: (...args: unknown[]) =>
			mockRecordedEvaluationResults(...args),
		recordedEvaluationUsage: (...args: unknown[]) =>
			mockRecordedEvaluationUsage(...args),
		candidate: vi.fn(),
		execution: vi.fn(),
		results: vi.fn().mockResolvedValue([]),
		cases: vi.fn().mockResolvedValue([]),
		matrix: vi.fn(),
	},
}));

vi.mock("@/hooks/useAgentPlatformUpdates", () => ({
	useAgentPlatformUpdates: vi.fn(),
}));

vi.mock("@/components/agents/evaluation/ChangesWorkspace", () => ({
	ChangesWorkspace: ({
		suiteId,
		executionId,
	}: {
		suiteId?: string;
		executionId?: string;
	}) => (
		<div>
			Changes suite {suiteId || "none"} execution {executionId || "none"}
		</div>
	),
}));

vi.mock("@/components/ai/ModelProfileSelector", () => ({
	ModelProfileSelector: ({
		value,
		onValueChange,
	}: {
		value?: string;
		onValueChange?: (value: string) => void;
	}) => (
		<label>
			Profile
			<select
				aria-label="Profile"
				value={value ?? ""}
				onChange={(event) => onValueChange?.(event.target.value)}
			>
				<option value="">Default assignment</option>
				<option value="profile-1">Careful reviewer</option>
			</select>
		</label>
	),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

const baseAgent = {
	id: "agent-1",
	name: "Test Parent Agent",
	system_prompt: "You are a helpful triage agent.",
	organization_id: null,
};

function makeTest(overrides: Record<string, unknown> = {}) {
	return {
		logical_test_id: "logical-1",
		version: 3,
		origin_suite_id: "suite-default",
		origin_suite_name: "Default collection",
		origin_is_default: true,
		case_id: "case-1",
		name: "Should ask before routing",
		position: 0,
		enabled: true,
		input: { situation: "User asks for a routing change." },
		fixture: {},
		simulator_policy: {},
		assertions: [{ label: "Asks before routing" }],
		expected_tools: [],
		forbidden_tools: ["route_ticket"],
		output_schema: null,
		repetitions: 1,
		scoring_policy: {},
		provenance: "manual",
		provenance_run_ids: [],
		finding_id: null,
		tags: [],
		created_at: "2026-09-19T00:00:00Z",
		updated_at: "2026-09-19T00:00:00Z",
		...overrides,
	};
}

function makeFinding() {
	return {
		id: "finding-1",
		agent_id: "agent-1",
		org_id: null,
		status: "open",
		description: "Routes without confirming.",
		expected_behavior: "Ask first.",
		source_kind: "run",
		source_run_id: "run-1",
		source_sequence: 7,
		external_ref: null,
		linked_case_ids: ["case-1"],
		created_by: null,
		created_at: "2026-09-19T00:00:00Z",
		updated_at: "2026-09-19T00:00:00Z",
	};
}

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
	vi.clearAllMocks();
	mockUseAgent.mockReturnValue({ data: baseAgent });
	mockUseAgentRuns.mockReturnValue({
		data: {
			items: [makeRun("run-1"), makeRun("run-2")],
			total: 2,
			next_cursor: null,
		},
		isLoading: false,
	});
	mockFindings.mockResolvedValue([makeFinding()]);
	mockReviews.mockResolvedValue({
		items: [
			{
				id: "review-1",
				agent_id: "agent-1",
				org_id: null,
				name: "Weekly quality review",
				status: "active",
				latest_version: 2,
				latest_version_id: "review-version-1",
				latest_version_created_at: "2026-09-19T00:00:00Z",
				created_by: null,
				created_at: "2026-09-19T00:00:00Z",
				updated_at: "2026-09-19T00:00:00Z",
			},
		],
		total: 1,
		limit: 50,
		offset: 0,
	});
	mockAgentTests.mockResolvedValue({
		items: [makeTest()],
		total: 1,
		limit: 50,
		offset: 0,
	});
	mockLatestAgentTests.mockResolvedValue({
		items: [
			{
				logical_test_id: "logical-1",
				version: 3,
				origin_suite_name: "Default collection",
				simulation: {
					execution_id: "execution-1",
					case_version: 3,
					profile_id: "profile-1",
					candidate_id: null,
					status: "failed",
					created_at: "2026-09-20T00:00:00Z",
				},
				recorded: {
					evaluation_id: "recorded-1",
					run_id: "run-1",
					case_version: 3,
					outcome: "failed",
					applicability: "applicable",
					judge_mode: "exact",
					created_at: "2026-09-20T00:00:00Z",
				},
			},
		],
		total: 1,
		limit: 50,
		offset: 0,
	});
	mockCreateAgentTest.mockResolvedValue(
		makeTest({ logical_test_id: "logical-2" }),
	);
	mockRunAgentTests.mockResolvedValue({
		job_id: "job-1",
		status: "queued",
		reused: false,
		notification_id: "notification-1",
		executionId: "execution-queued",
	});
	mockRecordedEvaluationResults.mockResolvedValue({
		evaluation_id: "recorded-1",
		job_id: "job-recorded-1",
		aggregate: { passed: 1, failed: 1, incomplete: 1 },
		results: [
			{
				id: "recorded-result-1",
				evaluation_id: "recorded-1",
				case_id: "case-1",
				case_version: 3,
				run_id: "run-1",
				applicability: "applicable",
				applicability_source: "judge",
				outcome: "failed",
				complete: false,
				assertion_outcomes: [],
				counts: { passed: 0, failed: 1 },
				evidence_refs: [],
				limitations: ["Run ended before final answer."],
				error: null,
				created_at: "2026-09-21T00:00:00Z",
			},
		],
		total: 1,
		limit: 50,
		offset: 0,
	});
	mockRecordedEvaluationUsage.mockResolvedValue({
		overall: {
			call_count: 2,
			total_cost: "0.0123",
			input_tokens: 10,
			output_tokens: 20,
			total_input_tokens: 10,
			total_output_tokens: 20,
			total_duration_ms: 100,
		},
		coverage: {
			started_attempt_count: 2,
			unobserved_attempt_count: 0,
			missing_cost_call_count: 1,
			unassigned_operation_call_count: 0,
			legacy_coverage_unknown: false,
			legacy_call_count: 0,
		},
		by_purpose: { items: [], total_groups: 0, limit: 50, offset: 0, omitted_group_count: 0 },
		by_provider_model: { items: [], total_groups: 0, limit: 50, offset: 0, omitted_group_count: 0 },
		by_profile: { items: [], total_groups: 0, limit: 50, offset: 0, omitted_group_count: 0 },
		by_organization: { items: [], total_groups: 0, limit: 50, offset: 0, omitted_group_count: 0 },
		by_operation: { items: [], total_groups: 0, limit: 50, offset: 0, omitted_group_count: 0 },
	});
});

function renderPage(entry = "/agents/agent-1/quality") {
	return renderWithProviders(
		<Routes>
			<Route
				path="/agents/:id/quality"
				element={<AgentQualityWorkbench />}
			/>
			<Route
				path="/agents/:agentId/runs/:runId"
				element={<div>run detail</div>}
			/>
		</Routes>,
		{ initialEntries: [entry] },
	);
}

describe("AgentQualityWorkbench", () => {
	it("defaults to the Tests collection and merges latest results", async () => {
		renderPage();

		expect(
			await screen.findByRole("heading", { name: "Workbench" }),
		).toBeVisible();
		expect(screen.queryByText(/quality workbench/i)).not.toBeInTheDocument();
		expect(document.querySelector("[data-agent-workbench]")).toBeVisible();
		expect(
			screen.getByText("Search, select, and inspect Workbench records."),
		).toBeVisible();
		expect(
		screen.queryByText("Search, select, and inspect quality signals."),
	).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Tests" })).toHaveAttribute(
			"aria-current",
			"page",
		);
		expect(
			screen.queryByRole("tab", { name: "Evidence" }),
		).not.toBeInTheDocument();
		expect(
			await screen.findByText("Should ask before routing"),
		).toBeVisible();
		expect(screen.getByText(/Simulation: failed/i)).toBeVisible();
		expect(screen.getByText(/Recorded: failed/i)).toBeVisible();
		expect(mockAgentTests).toHaveBeenCalledWith("agent-1", {
			limit: 50,
			offset: 0,
		});
		expect(mockLatestAgentTests).toHaveBeenCalledWith("agent-1", {
			limit: 50,
			offset: 0,
		});
	});

	it("maps old tab links to collection state without rendering wizard tabs", async () => {
		renderPage("/agents/agent-1/quality?tab=evidence&finding=finding-1");

		expect(
			await screen.findByRole("button", { name: "Findings" }),
		).toHaveAttribute("aria-current", "page");
		expect(
			await screen.findByText("Routes without confirming."),
		).toBeVisible();
		expect(
			screen.queryByRole("tab", { name: "Changes & Results" }),
		).not.toBeInTheDocument();
	});

	it("searches and multi-selects tests from the collection toolbar", async () => {
		const { user } = renderPage();
		await screen.findByText("Should ask before routing");

		await user.type(
			screen.getByLabelText("Search Tests"),
			"routing",
		);
		await user.click(
			screen.getByRole("checkbox", { name: /select should ask/i }),
		);

		expect(screen.getByText("1 selected")).toBeVisible();
		expect(screen.getByText("Should ask before routing")).toBeVisible();
	});

	it("opens test creation as an attached inspector from the toolbar", async () => {
		const { user } = renderPage();
		await screen.findByText("Should ask before routing");

		await user.click(screen.getByRole("button", { name: "Add Test" }));

		expect(screen.getByRole("heading", { name: "Improve agent" })).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Close inspector" }),
		).toBeVisible();
	});

	it("keeps the collection and selected inspector in one contained Workbench", async () => {
		const { user } = renderPage("/agents/agent-1/quality?collection=findings");

		await user.click(await screen.findByText("Routes without confirming."));

		expect(screen.getByLabelText("Workbench collections")).toBeVisible();
		expect(document.querySelector("[data-workspace-header]")).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Close inspector" }),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: /routes without confirming/i }),
		).toHaveAttribute("aria-selected", "true");

		await user.click(screen.getByRole("button", { name: "Close inspector" }));
		expect(
			screen.getByRole("button", { name: /routes without confirming/i }),
		).toHaveAttribute("aria-selected", "true");
	});

	it("creates a plain-language test from finding context and can clear it", async () => {
		const { user } = renderPage(
			"/agents/agent-1/quality?collection=tests&finding=finding-1",
		);

		await screen.findByText(/Drafting from finding/i);
		expect(screen.getByText("Routes without confirming.")).toBeVisible();
		await user.type(
			screen.getByLabelText("Situation"),
			"When a ticket asks to reroute",
		);
		await user.type(
			screen.getByLabelText("Expected behavior"),
			"Ask before routing",
		);
		await user.click(screen.getByRole("button", { name: "Create test" }));

		await waitFor(() => {
			expect(mockCreateAgentTest).toHaveBeenCalledWith(
				"agent-1",
				expect.objectContaining({
					name: "Should ask before routing",
					input: {
						situation: "When a ticket asks to reroute",
						expected_behavior: "Ask before routing",
					},
					assertions: [
						expect.objectContaining({
							type: "terminal_status",
							label: "Completes successfully",
							params: { status: "completed" },
						}),
					],
					finding_id: "finding-1",
					provenance: "finding",
				}),
			);
		});

		await user.click(screen.getByRole("button", { name: "Clear finding" }));
		expect(
			screen.queryByText(/Drafting from finding/i),
		).not.toBeInTheDocument();
	});

	it("applies supported Advanced JSON fields instead of burying text in assertions", async () => {
		const { user } = renderPage();
		await screen.findByText("Should ask before routing");
		await user.click(screen.getByRole("button", { name: "Add Test" }));

		await user.type(screen.getByLabelText("Situation"), "When tools are risky");
		await user.type(screen.getByLabelText("Expected behavior"), "Ask first");
		await user.click(screen.getByText("Advanced JSON/checks"));
		await user.click(screen.getByLabelText("Advanced JSON"));
		await user.paste(
			JSON.stringify({
				expected_tools: ["ask_user"],
				forbidden_tools: ["route_ticket"],
				fixture: { priority: "high" },
				simulator_policy: { persona: "impatient" },
				assertions: [
					{ type: "tool_not_called", params: { tool: "route_ticket" } },
				],
				output_schema: { type: "object" },
				repetitions: 2,
				scoring_policy: { mode: "all" },
				tags: ["safety"],
			}),
		);
		await user.click(screen.getByRole("button", { name: "Create test" }));

		await waitFor(() => {
			expect(mockCreateAgentTest).toHaveBeenCalledWith(
				"agent-1",
				expect.objectContaining({
					expected_tools: ["ask_user"],
					forbidden_tools: ["route_ticket"],
					fixture: { priority: "high" },
					simulator_policy: { persona: "impatient" },
					assertions: [
						{
							type: "tool_not_called",
							params: { tool: "route_ticket" },
						},
					],
					output_schema: { type: "object" },
					repetitions: 2,
					scoring_policy: { mode: "all" },
					tags: ["safety"],
				}),
			);
		});
		expect(
			mockCreateAgentTest.mock.calls.at(-1)?.[1].assertions[0],
		).not.toHaveProperty("notes");
	});

	it("blocks create when Advanced JSON is invalid or not an object", async () => {
		const { user } = renderPage();
		await screen.findByText("Should ask before routing");
		await user.click(screen.getByRole("button", { name: "Add Test" }));

		await user.type(screen.getByLabelText("Situation"), "When JSON is bad");
		await user.type(screen.getByLabelText("Expected behavior"), "Do not save");
		await user.click(screen.getByText("Advanced JSON/checks"));
		await user.click(screen.getByLabelText("Advanced JSON"));
		await user.paste("[1]");
		await user.click(screen.getByRole("button", { name: "Create test" }));

		expect(await screen.findByText(/must be a JSON object/i)).toBeVisible();
		expect(mockCreateAgentTest).not.toHaveBeenCalled();
	});

	it("runs selected test versions through runAgentTests without auto-running", async () => {
		const { user } = renderPage();
		await screen.findByText("Should ask before routing");
		expect(mockRunAgentTests).not.toHaveBeenCalled();

		await user.click(
			screen.getByRole("checkbox", { name: /select should ask/i }),
		);
		await user.selectOptions(screen.getByLabelText("Profile"), "profile-1");
		await user.click(screen.getByRole("button", { name: "Run Simulation" }));

		await waitFor(() => {
			expect(mockRunAgentTests).toHaveBeenCalledWith("agent-1", {
				selections: [{ case_id: "case-1", case_version: 3 }],
				profile_id: "profile-1",
				candidate_id: null,
			});
		});
		expect(
			screen.getByText(/Queued simulation execution-queued/i),
		).toBeVisible();
		expect(
			await screen.findByText(/Changes suite suite-default execution execution-queued/i),
		).toBeVisible();
	});

	it("prevents cross-suite simulation selections", async () => {
		mockAgentTests.mockResolvedValueOnce({
			items: [
				makeTest(),
				makeTest({
					logical_test_id: "logical-2",
					case_id: "case-2",
					name: "Should summarize safely",
					origin_suite_id: "suite-other",
					origin_suite_name: "Other collection",
				}),
			],
			total: 2,
			limit: 50,
			offset: 0,
		});
		const { user } = renderPage();
		await screen.findByText("Should summarize safely");

		await user.click(
			screen.getByRole("checkbox", { name: /select should ask/i }),
		);
		await user.click(
			screen.getByRole("checkbox", {
				name: /select should summarize safely/i,
			}),
		);
		await user.click(screen.getByRole("button", { name: "Run Simulation" }));

		expect(
			await screen.findByText(/Choose tests from one collection/i),
		).toBeVisible();
		expect(mockRunAgentTests).not.toHaveBeenCalled();
	});

	it("shows an inspector and returns to the list without losing query context", async () => {
		const { user } = renderPage(
			"/agents/agent-1/quality?collection=findings",
		);
		await user.click(await screen.findByText("Routes without confirming."));

		expect(
			screen.getByRole("heading", { name: "Finding details" }),
		).toBeVisible();
		expect(screen.getByRole("link", { name: /Run run-1/i })).toHaveAttribute(
			"href",
			"/agents/agent-1/runs/run-1?tab=activity&sequence=7",
		);
		await user.click(
			screen.getByRole("button", { name: "Create test from finding" }),
		);
		expect(screen.getByRole("button", { name: "Tests" })).toHaveAttribute(
			"aria-current",
			"page",
		);
		expect(screen.getByText(/Drafting from finding/i)).toBeVisible();

		await user.click(screen.getByRole("button", { name: "Findings" }));
		await user.click(await screen.findByText("Routes without confirming."));
		await user.click(screen.getByRole("button", { name: "Back to list" }));

		expect(screen.getByRole("button", { name: "Findings" })).toHaveAttribute(
			"aria-current",
			"page",
		);
	});

	it("loads recorded query results and usage in the inspector", async () => {
		renderPage("/agents/agent-1/quality?recorded=recorded-1");

		expect(
			await screen.findByRole("heading", { name: "Recorded results" }),
		).toBeVisible();
		expect(mockRecordedEvaluationResults).toHaveBeenCalledWith(
			"recorded-1",
			{ limit: 50, offset: 0 },
		);
		expect(mockRecordedEvaluationUsage).toHaveBeenCalledWith("recorded-1", {
			limit: 50,
			offset: 0,
		});
		expect(screen.getAllByText(/failed/i).length).toBeGreaterThan(0);
		expect(screen.getByText(/incomplete/i)).toBeVisible();
		expect(screen.getByText(/Run ended before final answer/i)).toBeVisible();
		expect(screen.getByText(/Unknown-cost calls: 1/i)).toBeVisible();
	});

	it("offers retry actions for collection read errors", async () => {
		mockAgentTests.mockRejectedValueOnce(new Error("boom"));
		const { user } = renderPage();

		await user.click(await screen.findByRole("button", { name: "Retry tests" }));

		await waitFor(() => {
			expect(mockAgentTests).toHaveBeenCalledTimes(2);
		});
	});
});
