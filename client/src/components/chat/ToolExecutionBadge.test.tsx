/**
 * Component tests for ToolExecutionBadge.
 *
 * Cover:
 *   - status → label/icon behaviour (status class applied)
 *   - duration formatting (ms vs s)
 *   - inline details show input arguments, result, error, logs on expand
 *
 * PrettyInputDisplay is stubbed so we assert at the badge boundary, not on
 * the nested pretty-printer's DOM.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

vi.mock("@/components/execution/PrettyInputDisplay", () => ({
	PrettyInputDisplay: ({
		inputData,
	}: {
		inputData: Record<string, unknown>;
	}) => <div data-testid="pretty-input">{JSON.stringify(inputData)}</div>,
}));

import { ToolExecutionBadge } from "./ToolExecutionBadge";
import type { components } from "@/lib/v1";
import type { ToolExecutionStatus } from "./ToolExecutionCard";

type ToolCall = components["schemas"]["ToolCall"];

function makeToolCall(overrides: Partial<ToolCall> = {}): ToolCall {
	return {
		id: "tc-1",
		name: "read_file",
		arguments: { path: "foo.txt" },
		...overrides,
	};
}

describe("ToolExecutionBadge — status rendering", () => {
	it.each<[ToolExecutionStatus, RegExp]>([
		["pending", /text-muted-foreground/],
		["running", /text-blue-500/],
		["success", /text-green-500/],
		["failed", /text-destructive/],
		["timeout", /text-amber-500/],
	])("applies the correct icon class for status=%s", (status, classRe) => {
		const { container } = renderWithProviders(
			<ToolExecutionBadge toolCall={makeToolCall()} status={status} />,
		);
		// Status icon is the first SVG within the badge trigger.
		const icon = container.querySelector("svg");
		expect(icon).not.toBeNull();
		expect(icon?.getAttribute("class") || "").toMatch(classRe);
	});

	it("formats sub-second durations in ms and second-plus in s", () => {
		const { rerender } = renderWithProviders(
			<ToolExecutionBadge
				toolCall={makeToolCall()}
				status="success"
				durationMs={500}
			/>,
		);
		expect(screen.getByText("500ms")).toBeInTheDocument();

		rerender(
			<ToolExecutionBadge
				toolCall={makeToolCall()}
				status="success"
				durationMs={2500}
			/>,
		);
		expect(screen.getByText("2.5s")).toBeInTheDocument();
	});

	it("shows the tool call name on the badge", () => {
		renderWithProviders(
			<ToolExecutionBadge
				toolCall={makeToolCall({ name: "grep_code" })}
				status="pending"
			/>,
		);
		const label = screen.getByText("grep_code");
		expect(label).toBeInTheDocument();
		expect(label.closest("button")).toHaveClass("min-h-11", "sm:min-h-7");
	});
});

describe("ToolExecutionBadge — inline details", () => {
	it("expands full-width details in place and shows the result", async () => {
		const { user } = renderWithProviders(
			<ToolExecutionBadge
				toolCall={makeToolCall()}
				status="success"
				result={{ matched: 3 }}
			/>,
		);

		expect(screen.queryByText(/^result$/i)).not.toBeInTheDocument();
		await user.click(screen.getByText("read_file"));

		const resultHeading = await screen.findByText(/^result$/i);
		const resultSection = resultHeading.parentElement!;
		expect(resultSection).toHaveTextContent(
			JSON.stringify({ matched: 3 }),
		);
		expect(resultHeading.closest(".rounded-xl")).toHaveClass("w-full");
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});

	it("shows error text and hides the result when error is set", async () => {
		const { user } = renderWithProviders(
			<ToolExecutionBadge
				toolCall={makeToolCall()}
				status="failed"
				error="boom"
				result={{ ignored: true }}
			/>,
		);

		await user.click(screen.getByText("read_file"));

		expect(await screen.findByText("boom")).toBeInTheDocument();
		expect(screen.queryByText(/result/i)).not.toBeInTheDocument();
	});

	it("renders log messages by level when provided", async () => {
		const { user } = renderWithProviders(
			<ToolExecutionBadge
				toolCall={makeToolCall()}
				status="success"
				result={{}}
				logs={[
					{ level: "info", message: "starting" },
					{ level: "error", message: "oh no" },
				]}
			/>,
		);
		await user.click(screen.getByText("read_file"));

		expect(await screen.findByText("starting")).toBeInTheDocument();
		expect(await screen.findByText("oh no")).toBeInTheDocument();
	});
});
