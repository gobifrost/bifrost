import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { QueryClient } from "@tanstack/react-query";

import { RunFindingAction } from "./RunFindingAction";
import type { components } from "@/lib/v1";

const mockFindings = vi.fn();
const mockCreateFinding = vi.fn();

vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		findings: (...args: unknown[]) => mockFindings(...args),
		createFinding: (...args: unknown[]) => mockCreateFinding(...args),
	},
}));

type Run = components["schemas"]["AgentRunDetailResponse"];
type Finding = components["schemas"]["FindingPublic"];

function makeRun(overrides: Partial<Run> = {}): Run {
	const run = {
		id: "run-1",
		agent_id: "agent-1",
		agent_name: "Triage",
		trigger_type: "manual",
		status: "completed",
		iterations_used: 1,
		tokens_used: 100,
		asked: "Help",
		did: "Answered",
		input: {},
		output: {},
		verdict: "down",
		verdict_note: "Skipped approval",
		created_at: "2026-09-21T00:00:00Z",
		steps: [],
		child_run_ids: [],
		child_runs: [],
		ai_usage: [],
		ai_totals: null,
		...overrides,
	};
	return {
		...run,
		summary_status: run.summary_status ?? "completed",
	};
}

function makeFinding(overrides: Partial<Finding> = {}): Finding {
	return {
		id: "finding-1",
		agent_id: "agent-1",
		status: "open",
		description: "Skipped approval",
		expected_behavior: null,
		source_kind: "run",
		source_run_id: "run-1",
		source_sequence: null,
		external_ref: null,
		finding_kind: "problem",
		linked_case_ids: [],
		...overrides,
	};
}

beforeEach(() => {
	mockFindings.mockReset();
	mockCreateFinding.mockReset();
	mockFindings.mockResolvedValue([]);
	mockCreateFinding.mockResolvedValue(makeFinding());
});

function renderAction(run = makeRun()) {
	return renderWithProviders(<RunFindingAction run={run} note="Skipped approval" />);
}

describe("RunFindingAction", () => {
	it("creates a prefilled Finding for a negatively reviewed run", async () => {
		const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
		const setQueryDataSpy = vi.spyOn(QueryClient.prototype, "setQueryData");
		const { user } = renderAction();

		await user.click(
			await screen.findByRole("button", { name: "Create Finding" }),
		);

		expect(mockCreateFinding).toHaveBeenCalledWith({
			agent_id: "agent-1",
			description: "Skipped approval",
			expected_behavior: null,
			source_kind: "run",
			finding_kind: "problem",
			source_run_id: "run-1",
			source_sequence: null,
			external_ref: null,
		});
		expect(
			await screen.findByRole("link", { name: "Open Finding" }),
		).toHaveAttribute(
			"href",
			"/agents/agent-1/quality?collection=findings&selected=findings:finding-1",
		);
		expect(setQueryDataSpy).not.toHaveBeenCalled();
		expect(invalidateSpy).toHaveBeenCalledWith({
			queryKey: ["agent-platform", "findings"],
		});
		expect(invalidateSpy).not.toHaveBeenCalledWith({
			queryKey: ["agent-quality", "findings"],
		});
	});

	it("links to an existing run-sourced Finding", async () => {
		mockFindings.mockResolvedValue([
			makeFinding(),
			makeFinding({
				id: "manual-finding",
				source_kind: "manual",
				source_run_id: null,
			}),
		]);

		renderAction();

		expect(
			await screen.findByRole("link", { name: "Open Finding" }),
		).toHaveAttribute(
			"href",
			"/agents/agent-1/quality?collection=findings&selected=findings:finding-1",
		);
		expect(
			screen.queryByRole("button", { name: "Create Finding" }),
		).not.toBeInTheDocument();
	});

	it("keeps a failed creation retryable without double-submitting", async () => {
		mockCreateFinding
			.mockRejectedValueOnce(new Error("Nope"))
			.mockResolvedValueOnce(makeFinding({ id: "finding-2" }));
		const { user } = renderAction();

		await user.click(
			await screen.findByRole("button", { name: "Create Finding" }),
		);
		expect(
			await screen.findByText(/could not create finding/i),
		).toBeVisible();
		expect(mockCreateFinding).toHaveBeenCalledTimes(1);

		await user.click(
			screen.getByRole("button", { name: "Retry Create Finding" }),
		);

		await waitFor(() => expect(mockCreateFinding).toHaveBeenCalledTimes(2));
		expect(mockCreateFinding).toHaveBeenNthCalledWith(2, {
			agent_id: "agent-1",
			description: "Skipped approval",
			expected_behavior: null,
			source_kind: "run",
			finding_kind: "problem",
			source_run_id: "run-1",
			source_sequence: null,
			external_ref: null,
		});
		expect(
			await screen.findByRole("link", { name: "Open Finding" }),
		).toHaveAttribute(
			"href",
			"/agents/agent-1/quality?collection=findings&selected=findings:finding-2",
		);
	});

	it("blocks creation and retries when existing Finding lookup fails", async () => {
		mockFindings
			.mockRejectedValueOnce(new Error("lookup failed"))
			.mockResolvedValueOnce([]);
		const { user } = renderAction();

		expect(
			await screen.findByText(/could not check existing findings/i),
		).toBeVisible();
		expect(
			screen.queryByRole("button", { name: "Create Finding" }),
		).not.toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Retry Finding lookup" }),
		);

		expect(
			await screen.findByRole("button", { name: "Create Finding" }),
		).toBeEnabled();
		expect(mockCreateFinding).not.toHaveBeenCalled();
	});
});
