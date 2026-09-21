import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

import { AgentRunSheet } from "./AgentRunSheet";
import type { Verdict } from "./RunReviewPanel";

const mockUseAgentRun = vi.fn();
const mockUseFlagConversation = vi.fn();
const mockSendFlagMessage = vi.fn();
const mockSetVerdict = vi.fn();
const mockClearVerdict = vi.fn();

vi.mock("@/services/agentRuns", () => ({
	useAgentRun: (id: string | undefined) => mockUseAgentRun(id),
	useFlagConversation: (id: string | undefined) =>
		mockUseFlagConversation(id),
	useSendFlagMessage: () => ({
		mutate: mockSendFlagMessage,
		isPending: false,
	}),
	useSetVerdict: () => ({
		mutate: mockSetVerdict,
	}),
	useClearVerdict: () => ({
		mutate: mockClearVerdict,
	}),
}));

vi.mock("./RunReviewSheet", () => ({
	RunReviewSheet: ({
		open,
		run,
		verdict,
		onVerdict,
		chatDisabled,
		defaultTab,
	}: {
		open: boolean;
		run: { id: string } | null;
		verdict: Verdict;
		onVerdict: (verdict: Verdict) => void;
		chatDisabled?: boolean;
		defaultTab?: string;
	}) =>
		open ? (
			<div
				data-testid="run-review-sheet"
				data-run-id={run?.id ?? ""}
				data-verdict={verdict ?? "none"}
				data-chat-disabled={String(chatDisabled)}
				data-default-tab={defaultTab ?? ""}
			>
				<button type="button" onClick={() => onVerdict("down")}>
					Mark wrong
				</button>
				<button type="button" onClick={() => onVerdict("up")}>
					Mark good
				</button>
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
	mockUseFlagConversation.mockReturnValue({
		data: undefined,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	mockSendFlagMessage.mockReset();
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

		expect(mockUseFlagConversation).toHaveBeenCalledWith(undefined);
		expect(screen.getByTestId("run-review-sheet")).toHaveAttribute(
			"data-default-tab",
			"review",
		);
		expect(screen.getByTestId("run-review-sheet")).toHaveAttribute(
			"data-chat-disabled",
			"true",
		);
	});

	it("does not load an improvement conversation while run details are loading", () => {
		mockUseAgentRun.mockReturnValue({
			data: undefined,
			isError: false,
			isFetching: true,
			refetch: vi.fn(),
		});

		renderSheet();

		expect(mockUseFlagConversation).toHaveBeenCalledWith(undefined);
	});

	it("loads the improvement conversation for a flagged run", () => {
		mockUseAgentRun.mockReturnValue({
			data: makeRun("down"),
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});

		renderSheet();

		expect(mockUseFlagConversation).toHaveBeenCalledWith("run-1");
		expect(screen.getByTestId("run-review-sheet")).toHaveAttribute(
			"data-default-tab",
			"tune",
		);
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
