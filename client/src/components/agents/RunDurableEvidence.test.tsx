import { describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { RunDurableEvidence } from "./RunDurableEvidence";
import { agentPlatform } from "@/services/agentPlatform";

vi.mock("@/hooks/useAgentPlatformUpdates", () => ({
	useAgentPlatformUpdates: vi.fn(),
}));
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		snapshot: vi.fn(),
		tree: vi.fn(),
		timeline: vi.fn(),
		checkpoints: vi.fn(),
	},
}));

function mockEmptyRun() {
	vi.mocked(agentPlatform.snapshot).mockResolvedValue({
		run_id: "run",
		status: "completed",
		attempt: 1,
		checkpoint_sequence: 0,
	});
	vi.mocked(agentPlatform.tree).mockResolvedValue({
		requested_run_id: "run",
		root_run_id: "run",
		total_runs: 1,
		truncated: false,
		root: {
			run_id: "run",
			agent_id: "agent",
			agent_name: "Support",
			status: "completed",
			depth: 0,
			attempt: 1,
		},
	});
	vi.mocked(agentPlatform.timeline).mockResolvedValue({
		run_id: "run",
		include_descendants: false,
		entries: [],
	});
	vi.mocked(agentPlatform.checkpoints).mockResolvedValue({
		run_id: "run",
		checkpoints: [],
	});
}

describe("RunDurableEvidence", () => {
	it("keeps the legacy snapshot note and hides empty infrastructure fields", async () => {
		mockEmptyRun();
		renderWithProviders(
			<RunDurableEvidence runId="run" agentId="agent" />,
			{ initialEntries: ["/agents/agent/runs/run?tab=activity"] },
		);
		expect(
			await screen.findByText(/older run has no saved snapshot/),
		).toBeVisible();
		expect(screen.getByText("Journal timeline")).toBeVisible();
		expect(screen.queryByText("Delegated runs")).not.toBeInTheDocument();
		expect(screen.queryByText("Lease owner")).not.toBeInTheDocument();
		expect(screen.queryByText("Lease expires")).not.toBeInTheDocument();
		expect(
			screen.getByText("No durable checkpoints recorded."),
		).toBeVisible();
	});

	it("reads the durable pause as waiting-until with the timer reason", async () => {
		mockEmptyRun();
		vi.mocked(agentPlatform.snapshot).mockResolvedValue({
			run_id: "run",
			status: "sleeping",
			attempt: 1,
			checkpoint_sequence: 2,
			wake_at: "2026-09-20T10:00:00Z",
		});
		vi.mocked(agentPlatform.timeline).mockResolvedValue({
			run_id: "run",
			include_descendants: false,
			entries: [
				{
					sequence: 7,
					kind: "timer",
					run_id: "run",
					attempt: 1,
					created_at: "2026-09-19T10:00:00Z",
					summary: "sleep requested",
					detail: { reason: "cooldown before retry" },
				},
			],
		});
		renderWithProviders(
			<RunDurableEvidence runId="run" agentId="agent" />,
			{ initialEntries: ["/agents/agent/runs/run?tab=activity"] },
		);
		const waiting = await screen.findByText(/Waiting until/);
		expect(waiting).toBeVisible();
		expect(waiting.textContent).toMatch(/cooldown before retry/);
	});

	it("highlights the linked journal sequence and keeps run identity", async () => {
		mockEmptyRun();
		vi.mocked(agentPlatform.timeline).mockResolvedValue({
			run_id: "run",
			include_descendants: false,
			entries: [
				{
					sequence: 17,
					kind: "timer",
					run_id: "run",
					attempt: 1,
					created_at: "2026-09-19T10:00:00Z",
					summary: "sleep requested",
					detail: {},
				},
			],
		});
		renderWithProviders(
			<RunDurableEvidence
				runId="run"
				agentId="agent"
				highlightSequence={17}
			/>,
			{ initialEntries: ["/agents/agent/runs/run?tab=activity"] },
		);
		expect(await screen.findByText("sleep requested")).toBeVisible();
		expect(
			screen.getByRole("link", { name: "Link to sequence 17" }),
		).toHaveAttribute("href", "/agents/agent/runs/run?tab=activity&sequence=17");
	});

	it("passes the timeline kind and attempt filters to the loaded collection", async () => {
		mockEmptyRun();
		const { user } = renderWithProviders(
			<RunDurableEvidence runId="run" agentId="agent" />,
			{ initialEntries: ["/agents/agent/runs/run?tab=activity"] },
		);
		await screen.findByText("Journal timeline");
		await user.type(screen.getByLabelText("Event kind"), "timer");
		await user.type(screen.getByLabelText("Attempt"), "2");
		await waitFor(() =>
			expect(agentPlatform.timeline).toHaveBeenCalledWith(
				"run",
				expect.objectContaining({ kind: "timer", attempt: 2 }),
			),
		);
	});
});
