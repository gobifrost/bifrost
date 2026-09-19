import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { TestDesigner } from "./TestDesigner";
import { agentPlatform } from "@/services/agentPlatform";
vi.mock("@/hooks/useAgentPlatformUpdates", () => ({
	useAgentPlatformUpdates: vi.fn(),
}));
vi.mock("@/services/agentRuns", () => ({
	useInfiniteAgentRuns: () => ({
		data: { pages: [{ items: [], total: 0 }] },
	}),
}));
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: { snapshot: vi.fn(), designer: vi.fn() },
}));
describe("Designer completion", () => {
	it("does not confuse model completion with reviewable materialized drafts", async () => {
		vi.mocked(agentPlatform.snapshot).mockResolvedValue({
			run_id: "run",
			status: "completed",
			attempt: 1,
			checkpoint_sequence: 1,
			correlation: {},
		});
		renderWithProviders(
			<TestDesigner
				suiteId="suite"
				agentId="agent"
				runId="run"
				onQueued={vi.fn()}
			/>,
		);
		expect(
			await screen.findByText(
				"Model completed. Preparing review drafts…",
			),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Designer in progress" }),
		).toBeDisabled();
	});
	it("surfaces materialization failure and allows a corrected generation", async () => {
		vi.mocked(agentPlatform.snapshot).mockResolvedValue({
			run_id: "run",
			status: "completed",
			attempt: 1,
			checkpoint_sequence: 1,
			correlation: {
				designer_materialized: true,
				designer_error: "Generated cases failed validation.",
			},
		});
		renderWithProviders(
			<TestDesigner
				suiteId="suite"
				agentId="agent"
				runId="run"
				onQueued={vi.fn()}
			/>,
		);
		expect(
			await screen.findByText("Generated cases failed validation."),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Generate review drafts" }),
		).toBeEnabled();
	});
});

it("describes a terminal model failure without claiming generation is continuing", async () => {
	vi.mocked(agentPlatform.snapshot).mockResolvedValue({
		run_id: "run",
		status: "failed",
		attempt: 1,
		checkpoint_sequence: 0,
	});
	renderWithProviders(
		<TestDesigner
			suiteId="suite"
			agentId="agent"
			runId="run"
			onQueued={vi.fn()}
		/>,
	);
	expect(
		await screen.findByText(
			"Test Designer stopped before drafts were ready. Inspect the run, then generate corrected drafts.",
		),
	).toBeVisible();
	expect(
		screen.getByRole("button", { name: "Generate review drafts" }),
	).toBeEnabled();
});
