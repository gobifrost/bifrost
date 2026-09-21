import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import type { ReactNode } from "react";

import { AgentRunSheet } from "./AgentRunSheet";
import type { Verdict } from "./RunReviewPanel";

const mockUseAgentRun = vi.fn();
const mockSetVerdict = vi.fn();
const mockClearVerdict = vi.fn();

vi.mock("@/services/agentRuns", () => ({
	useAgentRun: (id: string | undefined) => mockUseAgentRun(id),
	useSetVerdict: () => ({
		mutate: mockSetVerdict,
	}),
	useClearVerdict: () => ({
		mutate: mockClearVerdict,
	}),
}));

vi.mock("./RunFindingAction", () => ({
	RunFindingAction: () => <button type="button">Create Finding</button>,
}));

vi.mock("./RunReviewSheet", () => ({
	RunReviewSheet: ({
		open,
		run,
		verdict,
		onVerdict,
		reviewActions,
	}: {
		open: boolean;
		run: { id: string } | null;
		verdict: Verdict;
		onVerdict: (verdict: Verdict) => void;
		reviewActions?: ReactNode;
	}) =>
		open ? (
			<div
				data-testid="run-review-sheet"
				data-run-id={run?.id ?? ""}
				data-verdict={verdict ?? "none"}
			>
				<button type="button" onClick={() => onVerdict("down")}>
					Mark wrong
				</button>
				<button type="button" onClick={() => onVerdict("up")}>
					Mark good
				</button>
				{reviewActions}
			</div>
		) : null,
}));

function makeRun(verdict: Verdict = null) {
	return {
		id: "run-1",
		agent_id: "agent-1",
		agent_name: "Triage",
		status: "completed",
		trigger_type: "manual",
		asked: "Help",
		did: "Answered",
		verdict,
		verdict_note: null,
		created_at: "2026-09-21T00:00:00Z",
		started_at: "2026-09-21T00:00:00Z",
		input: {},
		output: {},
		steps: [],
		child_run_ids: [],
		child_runs: [],
		ai_usage: [],
		ai_totals: null,
	};
}

beforeEach(() => {
	mockUseAgentRun.mockReturnValue({
		data: makeRun(null),
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	mockSetVerdict.mockReset();
	mockClearVerdict.mockReset();
});

function renderSheet(openRunId = "run-1") {
	return renderWithProviders(
		<AgentRunSheet openRunId={openRunId} onClose={vi.fn()} />,
	);
}

describe("AgentRunSheet", () => {
	it("does not load an improvement conversation for an unreviewed run", () => {
		renderSheet();

		expect(screen.getByTestId("run-review-sheet")).toHaveAttribute(
			"data-verdict",
			"none",
		);
		expect(
			screen.queryByRole("button", { name: "Create Finding" }),
		).not.toBeInTheDocument();
	});

	it("does not load an improvement conversation while run details are loading", () => {
		mockUseAgentRun.mockReturnValue({
			data: undefined,
			isError: false,
			isFetching: true,
			refetch: vi.fn(),
		});

		renderSheet();

		expect(screen.getByTestId("run-review-sheet")).toHaveAttribute(
			"data-run-id",
			"",
		);
	});

	it("shows a Finding action instead of loading a conversation for a flagged run", () => {
		mockUseAgentRun.mockReturnValue({
			data: { ...makeRun("down"), verdict_note: "Skipped approval" },
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});

		renderSheet();

		expect(screen.getByTestId("run-review-sheet")).toHaveAttribute(
			"data-verdict",
			"down",
		);
		expect(
			screen.getByRole("button", { name: "Create Finding" }),
		).toBeInTheDocument();
	});

	it("preserves verdict changes while conversation loading is gated", async () => {
		mockSetVerdict.mockImplementation((_payload, callbacks) => {
			callbacks?.onSuccess?.();
			callbacks?.onSettled?.();
		});
		const { user } = renderSheet();

		await user.click(screen.getByRole("button", { name: "Mark wrong" }));

		expect(mockSetVerdict).toHaveBeenCalledWith(
			{
				params: { path: { run_id: "run-1" } },
				body: { verdict: "down", note: null },
			},
			expect.any(Object),
		);
	});
});
