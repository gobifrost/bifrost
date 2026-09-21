import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { SuiteWorkspace } from "./SuiteWorkspace";
import { agentPlatform } from "@/services/agentPlatform";

vi.mock("@/hooks/useAgentPlatformUpdates", () => ({
	useAgentPlatformUpdates: vi.fn(),
}));
vi.mock("@/services/agentRuns", () => ({
	useInfiniteAgentRuns: () => ({
		data: undefined,
		isLoading: false,
		hasNextPage: false,
	}),
}));
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		suites: vi.fn(),
		suite: vi.fn(),
		cases: vi.fn(),
		finding: vi.fn(),
		createSuite: vi.fn(),
		updateSuite: vi.fn(),
		publish: vi.fn(),
		createCase: vi.fn(),
		accept: vi.fn(),
		designer: vi.fn(),
	},
}));

function setupDraft() {
	vi.mocked(agentPlatform.suites).mockResolvedValue([]);
	vi.mocked(agentPlatform.suite).mockResolvedValue({
		id: "suite-1",
		agent_id: "agent",
		name: "Regression",
		description: "",
		status: "draft",
		version: 1,
	});
	vi.mocked(agentPlatform.cases).mockResolvedValue([]);
	vi.mocked(agentPlatform.finding).mockResolvedValue({
		id: "finding-1",
		agent_id: "agent",
		status: "open",
		description: "Routes without confirming.",
		expected_behavior: "Ask first.",
		source_kind: "run",
		finding_kind: "problem",
		source_run_id: "run-1",
		source_sequence: 3,
		external_ref: null,
		linked_case_ids: [],
	});
	vi.mocked(agentPlatform.createCase).mockResolvedValue({
		id: "case-1",
	} as never);
}

describe("SuiteWorkspace finding handoff", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});
	it("shows the finding while drafting and seeds an editable title", async () => {
		setupDraft();
		const onNavigate = vi.fn();
		const { user } = renderWithProviders(
			<SuiteWorkspace
				agentId="agent"
				agentName="Triage"
				organizationId={null}
				suiteId="suite-1"
				findingId="finding-1"
				onNavigate={onNavigate}
			/>,
			{ initialEntries: ["/agents/agent/quality?tab=tests"] },
		);
		expect(
			await screen.findByText("Routes without confirming."),
		).toBeVisible();
		expect(screen.getByText(/Expected: Ask first/)).toBeVisible();
		await user.click(screen.getByRole("button", { name: "Add test" }));
		expect(screen.getByLabelText("Test name")).toHaveValue(
			"Repro: Routes without confirming.",
		);
		await user.click(screen.getByRole("button", { name: "Save test" }));
		expect(agentPlatform.createCase).toHaveBeenCalledWith(
			"suite-1",
			expect.objectContaining({ finding_id: "finding-1" }),
		);
		expect(onNavigate).toHaveBeenCalledWith({ finding: undefined });
	});

	it("clears the finding context without saving", async () => {
		setupDraft();
		const onNavigate = vi.fn();
		const { user } = renderWithProviders(
			<SuiteWorkspace
				agentId="agent"
				agentName="Triage"
				organizationId={null}
				suiteId="suite-1"
				findingId="finding-1"
				onNavigate={onNavigate}
			/>,
			{ initialEntries: ["/agents/agent/quality?tab=tests"] },
		);
		await user.click(
			await screen.findByRole("button", { name: "Clear finding" }),
		);
		expect(onNavigate).toHaveBeenCalledWith({ finding: undefined });
		expect(agentPlatform.createCase).not.toHaveBeenCalled();
	});

	it("shows the test count and opens generation only on demand", async () => {
		setupDraft();
		vi.mocked(agentPlatform.cases).mockResolvedValue([
			{
				id: "case-1",
				suite_id: "suite-1",
				name: "Simple response",
				position: 0,
				enabled: true,
				version: 1,
				input: { message: "Return the fixture answer." },
				fixture: {
					allowed_tools: ["lookup"],
					rules: [
						{
							tool: "lookup",
							match_args: {},
							return: { items: [] },
						},
					],
				},
				assertions: [
					{
						type: "tool_called",
						label: "Lookup used",
						params: { tool: "lookup" },
					},
				],
				provenance: "manual",
				accepted: true,
				repetitions: 1,
			},
		]);
		const { user } = renderWithProviders(
			<SuiteWorkspace
				agentId="agent"
				agentName="Triage"
				organizationId={null}
				suiteId="suite-1"
				onNavigate={vi.fn()}
			/>,
			{ initialEntries: ["/agents/agent/quality?tab=tests"] },
		);
		expect(await screen.findByText(/1 test/)).toBeVisible();
		// Generation stays collapsed until requested.
		expect(
			screen.queryByLabelText("What should these cases test?"),
		).not.toBeVisible();
		await user.click(
			screen.getByText("Generate tests", { selector: "summary" }),
		);
		expect(
			screen.getByLabelText("What should these cases test?"),
		).toBeVisible();
		// Saved-test inspection reads without JSON.
		await user.click(
			screen.getByText("Inspect scenario, tool responses and expectations"),
		);
		expect(screen.getByText("Return the fixture answer.")).toBeVisible();
		expect(
			await screen.findByText(/Must call lookup/),
		).toBeVisible();
	});

	it("filters loaded tests honestly by search", async () => {
		setupDraft();
		vi.mocked(agentPlatform.cases).mockResolvedValue([
			{
				id: "case-1",
				suite_id: "suite-1",
				name: "Simple response",
				position: 0,
				enabled: true,
				version: 1,
				input: { message: "hi" },
				fixture: {},
				assertions: [],
				provenance: "manual",
				accepted: true,
				repetitions: 1,
			},
			{
				id: "case-2",
				suite_id: "suite-1",
				name: "Empty lookup",
				position: 1,
				enabled: true,
				version: 1,
				input: { message: "hi" },
				fixture: {},
				assertions: [],
				provenance: "manual",
				accepted: true,
				repetitions: 1,
			},
		]);
		const { user } = renderWithProviders(
			<SuiteWorkspace
				agentId="agent"
				agentName="Triage"
				organizationId={null}
				suiteId="suite-1"
				onNavigate={vi.fn()}
			/>,
			{ initialEntries: ["/agents/agent/quality?tab=tests"] },
		);
		expect(await screen.findByText("Simple response")).toBeVisible();
		await user.type(screen.getByLabelText("Search tests"), "empty");
		await waitFor(() =>
			expect(screen.queryByText("Simple response")).not.toBeInTheDocument(),
		);
		expect(screen.getByText("Empty lookup")).toBeVisible();
	});
});
