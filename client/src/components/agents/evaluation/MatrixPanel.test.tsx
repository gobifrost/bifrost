import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { MatrixPanel } from "./MatrixPanel";
import { agentPlatform } from "@/services/agentPlatform";
import { listModelProfiles } from "@/services/aiModels";

vi.mock("@/hooks/useAgentPlatformUpdates", () => ({
	useAgentPlatformUpdates: vi.fn(),
}));
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		candidate: vi.fn(),
		matrix: vi.fn(),
		executeBatch: vi.fn(),
		cancelMatrix: vi.fn(),
		results: vi.fn(),
		cases: vi.fn(),
	},
}));
vi.mock("@/services/aiModels", () => ({
	listModelProfiles: vi.fn(),
}));

function setup() {
	vi.mocked(listModelProfiles).mockResolvedValue([
		{
			id: "profile-1",
			name: "Fast",
			connection_id: "conn-1",
			model: "fast-1",
			enabled_for_chat: true,
			referenced_agent_count: 0,
			created_at: "2026-09-19T00:00:00Z",
			updated_at: "2026-09-19T00:00:00Z",
			connection: {
				id: "conn-1",
				name: "Test Conn",
				provider: "openai",
			},
		},
	]);
	vi.mocked(agentPlatform.candidate).mockResolvedValue({
		id: "candidate-1",
		org_id: null,
		base_agent_id: "agent",
		base_agent_updated_at: null,
		name: "Fix routing",
		overlays: { system_prompt: "Ask first." },
		snapshot: {},
		snapshot_hash: "abc",
		evaluation_only: true,
		created_by: null,
		created_at: null,
	});
	vi.mocked(agentPlatform.executeBatch).mockResolvedValue({
		matrix: {
			id: "matrix-1",
			suite_id: "suite-1",
			suite_version: 1,
			candidate_ids: ["candidate-1"],
			profile_ids: ["profile-1"],
			repetitions_override: null,
			org_id: null,
			created_by: null,
			created_at: null,
		},
		cells: [
			{
				execution_id: "exec-base",
				candidate_id: null,
				profile_id: "profile-1",
				status: "queued",
				total_cases: 1,
				completed_cases: 0,
				passed_cases: 0,
				failed_cases: 0,
				platform_job_id: "job-1",
				reused: false,
			},
			{
				execution_id: "exec-cand",
				candidate_id: "candidate-1",
				profile_id: "profile-1",
				status: "queued",
				total_cases: 1,
				completed_cases: 0,
				passed_cases: 0,
				failed_cases: 0,
				platform_job_id: "job-2",
				reused: false,
			},
		],
		planned_cells: 2,
		planned_runs: 3,
		cost_estimate_usd: null,
		cost_note: "Metered per run.",
	});
	vi.mocked(agentPlatform.matrix).mockResolvedValue({
		matrix: { id: "matrix-1", suite_id: "suite-1", suite_version: 1 },
		cells: [],
		planned_cells: 0,
		planned_runs: 0,
		cost_estimate_usd: null,
		cost_note: "",
	});
}

describe("MatrixPanel", () => {
	it("admits baseline-plus-candidate cells under the selected profile", async () => {
		setup();
		const onMatrix = vi.fn();
		const { user } = renderWithProviders(
			<MatrixPanel
				agentId="agent"
				suiteId="suite-1"
				candidateId="candidate-1"
				profileIds={["profile-1"]}
				matrixId=""
				onProfilesChange={() => {}}
				onMatrix={onMatrix}
			/>,
			{ initialEntries: ["/agents/agent/quality?tab=changes"] },
		);
		expect(await screen.findByText("Fast")).toBeVisible();
		await user.click(
			screen.getByRole("button", {
				name: "Run tests",
			}),
		);
		expect(agentPlatform.executeBatch).toHaveBeenCalledWith({
			suite_id: "suite-1",
			candidate_ids: ["candidate-1"],
			profile_ids: ["profile-1"],
		});
		expect(onMatrix).toHaveBeenCalledWith("matrix-1");
	});

	it("labels the saved run with profile names, not ids", async () => {
		setup();
		vi.mocked(agentPlatform.matrix).mockResolvedValue({
			matrix: {
				id: "matrix-1",
				suite_id: "suite-1",
				suite_version: 1,
				candidate_ids: ["candidate-1"],
				profile_ids: ["profile-1", "missing-profile"],
			},
			cells: [
				{
					execution_id: "exec-base",
					candidate_id: null,
					profile_id: "profile-1",
					status: "failed",
					total_cases: 1,
					completed_cases: 1,
					passed_cases: 0,
					failed_cases: 1,
					platform_job_id: "job-1",
					reused: false,
				},
				{
					execution_id: "exec-cand",
					candidate_id: "candidate-1",
					profile_id: "profile-1",
					status: "running",
					total_cases: 1,
					completed_cases: 0,
					passed_cases: 0,
					failed_cases: 0,
					platform_job_id: "job-2",
					reused: false,
				},
			],
			planned_cells: 2,
			planned_runs: 4,
			cost_estimate_usd: null,
			cost_note: "",
		});
		renderWithProviders(
			<MatrixPanel
				agentId="agent"
				suiteId="suite-1"
				candidateId="candidate-1"
				profileIds={["profile-1"]}
				matrixId="matrix-1"
				onProfilesChange={() => {}}
				onMatrix={() => {}}
			/>,
			{ initialEntries: ["/agents/agent/quality?tab=changes"] },
		);
		expect(await screen.findByText(/Saved run/)).toBeVisible();
		// Profile name resolves in the picker, the saved line, and cells.
		expect(screen.getAllByText(/Fast/).length).toBe(4);
		expect(screen.getByText(/Profile unavailable/)).toBeVisible();
		// Failures foreground over the green job state.
		expect(
			screen.getByText(/Needs attention: 1 of 2 comparisons failed/),
		).toBeVisible();
	});

	it("requires at least one profile before running", async () => {
		setup();
		renderWithProviders(
			<MatrixPanel
				agentId="agent"
				suiteId="suite-1"
				candidateId=""
				profileIds={[]}
				matrixId=""
				onProfilesChange={() => {}}
				onMatrix={() => {}}
			/>,
			{ initialEntries: ["/agents/agent/quality?tab=changes"] },
		);
		expect(
			await screen.findByRole("button", {
				name: "Run tests",
			}),
		).toBeDisabled();
	});

	it("states cancelled comparisons explicitly, never as running or passed", async () => {
		setup();
		vi.mocked(agentPlatform.matrix).mockResolvedValue({
			matrix: {
				id: "matrix-1",
				suite_id: "suite-1",
				suite_version: 1,
				candidate_ids: [],
				profile_ids: ["profile-1"],
			},
			cells: [
				{
					execution_id: "exec-cancelled-partial",
					candidate_id: null,
					profile_id: "profile-1",
					status: "cancelled",
					total_cases: 2,
					completed_cases: 1,
					passed_cases: 1,
					failed_cases: 0,
					platform_job_id: "job-1",
					reused: false,
				},
				{
					execution_id: "exec-cancelled-full",
					candidate_id: null,
					profile_id: "profile-1",
					status: "cancelled",
					total_cases: 1,
					completed_cases: 1,
					passed_cases: 1,
					failed_cases: 0,
					platform_job_id: "job-2",
					reused: false,
				},
			],
			planned_cells: 2,
			planned_runs: 3,
			cost_estimate_usd: null,
			cost_note: "",
		});
		renderWithProviders(
			<MatrixPanel
				agentId="agent"
				suiteId="suite-1"
				candidateId=""
				profileIds={["profile-1"]}
				matrixId="matrix-1"
				onProfilesChange={() => {}}
				onMatrix={() => {}}
			/>,
			{ initialEntries: ["/agents/agent/quality?tab=changes"] },
		);
		expect(await screen.findByText(/Saved run/)).toBeVisible();
		expect(
			screen.getByText(/Cancelled: 2 of 2 comparisons cancelled/),
		).toBeVisible();
		expect(screen.queryByText(/Running:/)).not.toBeInTheDocument();
		expect(screen.queryByText(/All .* passed/)).not.toBeInTheDocument();
	});
});
