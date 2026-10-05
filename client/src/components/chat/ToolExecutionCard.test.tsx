/**
 * Component tests for ToolExecutionCard.
 *
 * The card derives its status from a cascade: streaming state > API data
 * > legacy execution > has-result-message > pending. Cover the main
 * transitions and the expand-to-show-result interaction.
 *
 * Heavy dependencies (execution streaming, PrettyInputDisplay, framer-motion)
 * are stubbed so tests stay deterministic.
 */

import React from "react";
import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

// Execution data hooks: return no data by default so behavior falls through
// to props. Individual tests can override via mockReturnValue.
const mockUseExecution = vi.fn<
	() => { data: unknown; isLoading: boolean; error?: unknown }
>(() => ({ data: undefined, isLoading: false }));
const mockUseExecutionLogs = vi.fn<() => { data: unknown }>(() => ({
	data: undefined,
}));

vi.mock("@/hooks/useExecutions", async (importOriginal) => ({
	isNotFoundError: (
		await importOriginal<typeof import("@/hooks/useExecutions")>()
	).isNotFoundError,
	useExecution: () => mockUseExecution(),
	useExecutionLogs: () => mockUseExecutionLogs(),
}));

vi.mock("@/services/runRetention", () => ({
	useRunRetentionDays: () => 30,
}));

vi.mock("@/hooks/useExecutionStream", () => ({
	useExecutionStream: () => undefined,
}));

vi.mock("@/stores/executionStreamStore", () => ({
	useExecutionStreamStore: () => [] as unknown[],
}));

// Framer-motion: static div.
const mockUseReducedMotion = vi.hoisted(() => vi.fn(() => false));
vi.mock("framer-motion", () => {
	const passthrough = ({
		children,
		initial: _i,
		animate: _a,
		exit: _e,
		transition: _t,
		...rest
	}: Record<string, unknown> & { children?: React.ReactNode }) => (
		<div {...(rest as Record<string, unknown>)}>{children}</div>
	);
	return {
		motion: new Proxy({}, { get: () => passthrough }),
		AnimatePresence: ({ children }: { children: React.ReactNode }) => (
			<>{children}</>
		),
		useReducedMotion: () => mockUseReducedMotion(),
	};
});

// PrettyInputDisplay: simple stub that surfaces the payload for assertions.
vi.mock("@/components/execution/PrettyInputDisplay", () => ({
	PrettyInputDisplay: ({
		inputData,
	}: {
		inputData: Record<string, unknown>;
	}) => <div data-testid="pretty-input">{JSON.stringify(inputData)}</div>,
}));

import { ToolExecutionCard } from "./ToolExecutionCard";
import type { components } from "@/lib/v1";

type ToolCall = components["schemas"]["ToolCall"];

function makeToolCall(overrides: Partial<ToolCall> = {}): ToolCall {
	return {
		id: "tc-1",
		name: "run_task",
		arguments: { input: "data" },
		...overrides,
	};
}

describe("ToolExecutionCard — status transitions", () => {
	it("exposes timeout details and names its input action", () => {
		renderWithProviders(<ToolExecutionCard toolCall={makeToolCall()} isStreaming streamingState={{ status: "timeout", logs: [], error: "Timed out waiting for the service" }} />);
		expect(screen.getByRole("alert")).toHaveTextContent("Timed out waiting for the service");
		expect(screen.getByRole("button", { name: "Input parameters for run_task" })).toBeInTheDocument();
	});
	it("shows Pending when there's no execution data and no result yet", () => {
		renderWithProviders(
			<ToolExecutionCard toolCall={makeToolCall()} executionId={undefined} />,
		);
		expect(screen.getByText(/pending/i)).toBeInTheDocument();
	});

	it("shows Success when a result message is present (no execution_id)", () => {
		renderWithProviders(
			<ToolExecutionCard
				toolCall={makeToolCall()}
				executionId={undefined}
				hasResultMessage
			/>,
		);
		expect(screen.getByText(/success/i)).toBeInTheDocument();
	});

	it("maps API Running to the Running badge", () => {
		mockUseExecution.mockReturnValueOnce({
			data: { status: "Running" },
			isLoading: false,
		});
		renderWithProviders(
			<ToolExecutionCard toolCall={makeToolCall()} executionId="exec-1" />,
		);
		expect(screen.getByText(/running/i)).toBeInTheDocument();
	});

	it("maps API Failed to Failed and surfaces the error_message", () => {
		mockUseExecution.mockReturnValueOnce({
			data: {
				status: "Failed",
				error_message: "exec boom",
				duration_ms: 123,
			},
			isLoading: false,
		});
		renderWithProviders(
			<ToolExecutionCard toolCall={makeToolCall()} executionId="exec-1" />,
		);
		expect(screen.getByText(/failed/i)).toBeInTheDocument();
		expect(screen.getByText("exec boom")).toBeInTheDocument();
		expect(screen.getByText("123ms")).toBeInTheDocument();
	});

	it("uses streamingState over API data when isStreaming is true", () => {
		mockUseExecution.mockReturnValueOnce({
			data: { status: "Pending" },
			isLoading: false,
		});
		renderWithProviders(
			<ToolExecutionCard
				toolCall={makeToolCall()}
				executionId="exec-1"
				isStreaming
				streamingState={{
					status: "running",
					logs: [{ level: "info", message: "progress..." }],
				}}
			/>,
		);
		expect(screen.getByText(/running/i)).toBeInTheDocument();
		// Running view shows streamed log lines.
		expect(screen.getByText("progress...")).toBeInTheDocument();
	});
});

describe("ToolExecutionCard — result expansion", () => {
	it("shows a Result toggle on success and expands on click", async () => {
		mockUseExecution.mockReturnValue({
			data: { status: "Success", result: { value: 42 } },
			isLoading: false,
		});

		const { user } = renderWithProviders(
			<ToolExecutionCard toolCall={makeToolCall()} executionId="exec-1" />,
		);

		const toggle = screen.getByRole("button", { name: /result/i });
		expect(toggle).toBeInTheDocument();

		await user.click(toggle);

		// PrettyInputDisplay stub renders the JSON blob.
		const pretty = await screen.findByTestId("pretty-input");
		expect(pretty).toHaveTextContent(JSON.stringify({ value: 42 }));
	});

	it("scrolls running logs without smooth motion when reduced motion is enabled", () => {
		mockUseExecution.mockReturnValue({
			data: { status: "Running" },
			isLoading: false,
		});
		mockUseReducedMotion.mockReturnValue(true);
		const scrollTo = vi.fn();
		const originalScrollTo = Element.prototype.scrollTo;
		Object.defineProperty(Element.prototype, "scrollTo", {
			configurable: true,
			value: scrollTo,
		});

		renderWithProviders(
			<ToolExecutionCard
				toolCall={makeToolCall()}
				executionId="exec-1"
				isStreaming
				streamingState={{
					status: "running",
					logs: [{ level: "info", message: "progress..." }],
				}}
			/>,
		);

		expect(scrollTo).toHaveBeenCalledWith({ behavior: "auto", top: 0 });
		mockUseReducedMotion.mockReturnValue(false);
		Object.defineProperty(Element.prototype, "scrollTo", {
			configurable: true,
			value: originalScrollTo,
		});
	});
});

describe("ToolExecutionCard — removed execution", () => {
	const removed = {
		data: undefined,
		isLoading: false,
		error: {
			detail: "Execution exec-1 not found. Finished runs are removed after 30 days.",
		},
	};

	it("shows the tool call's own result once its execution is removed", async () => {
		mockUseExecution.mockReturnValue(removed);
		const { user } = renderWithProviders(
			<ToolExecutionCard
				toolCall={makeToolCall()}
				executionId="exec-1"
				hasResultMessage
				toolCallMessage={{
					tool_state: "completed",
					tool_result: { answer: 42 },
				}}
			/>,
		);

		expect(screen.getByText("Success")).toBeInTheDocument();
		expect(
			screen.getByText("Removed after 30 days (retention)"),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /result/i }));
		expect(await screen.findByTestId("pretty-input")).toHaveTextContent(
			JSON.stringify({ answer: 42 }),
		);
	});

	it("shows the tool call's own error once its execution is removed", () => {
		mockUseExecution.mockReturnValue(removed);
		renderWithProviders(
			<ToolExecutionCard
				toolCall={makeToolCall()}
				executionId="exec-1"
				hasResultMessage
				toolCallMessage={{
					tool_state: "error",
					tool_result: { error: "boom" },
				}}
			/>,
		);

		expect(screen.getByText("Failed")).toBeInTheDocument();
		expect(screen.getByRole("alert")).toHaveTextContent("boom");
		expect(
			screen.getByText("Removed after 30 days (retention)"),
		).toBeInTheDocument();
	});
});
