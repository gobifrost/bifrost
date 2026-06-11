import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExecutionsOverTimeCard } from "./ExecutionsOverTimeCard";
import {
	summarizeOutcomes,
	type BucketableExecution,
} from "@/lib/execution-buckets";

function renderCard(
	overrides: Partial<
		React.ComponentProps<typeof ExecutionsOverTimeCard>
	> = {},
) {
	const executions =
		"executions" in overrides
			? overrides.executions
			: ([] as BucketableExecution[]);
	const props = {
		window: "7d" as const,
		onWindowChange: vi.fn(),
		executions,
		// The card shares the stat cards' outcome summary in production
		// (Dashboard computes it once); mirror that wiring here.
		outcomes: summarizeOutcomes(executions ?? []),
		truncated: false,
		isLoading: false,
		isError: false,
		...overrides,
	};
	const result = render(
		<MemoryRouter>
			<ExecutionsOverTimeCard {...props} />
		</MemoryRouter>,
	);
	return { ...result, props };
}

describe("ExecutionsOverTimeCard", () => {
	it("shows a skeleton while loading", () => {
		const { container } = renderCard({
			isLoading: true,
			executions: undefined,
		});
		expect(
			container.querySelector('[data-slot="skeleton"]'),
		).toBeInTheDocument();
		expect(
			screen.queryByTestId("executions-chart-empty"),
		).not.toBeInTheDocument();
	});

	it("shows an error state when the fetch failed", () => {
		renderCard({ isError: true, executions: undefined });
		expect(
			screen.getByTestId("executions-chart-error"),
		).toBeInTheDocument();
	});

	it("shows an empty state when the window has no terminal runs", () => {
		renderCard({ executions: [] });
		expect(
			screen.getByTestId("executions-chart-empty"),
		).toBeInTheDocument();
		expect(
			screen.getByText("No executions in this window"),
		).toBeInTheDocument();
	});

	it("summarizes run totals and links the failed count to filtered history", () => {
		renderCard({
			executions: [
				{ status: "Success", started_at: new Date().toISOString() },
				{ status: "Success", started_at: new Date().toISOString() },
				{ status: "Failed", started_at: new Date().toISOString() },
			],
		});
		expect(screen.getByText(/Last 7 days · 3 runs/)).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: "1 failed" }),
		).toHaveAttribute("href", "/history?status=Failed");
		expect(
			screen.queryByTestId("executions-chart-empty"),
		).not.toBeInTheDocument();
	});

	it("omits the failed link when nothing failed", () => {
		renderCard({
			executions: [
				{ status: "Success", started_at: new Date().toISOString() },
			],
		});
		expect(screen.queryByRole("link")).not.toBeInTheDocument();
	});

	it("invokes onWindowChange when a window toggle is clicked", () => {
		const { props } = renderCard();
		fireEvent.click(screen.getByRole("radio", { name: "Last 24 hours" }));
		expect(props.onWindowChange).toHaveBeenCalledWith("24h");
	});

	it("annotates the subtitle when the window fetch was truncated", () => {
		renderCard({
			executions: [
				{ status: "Success", started_at: new Date().toISOString() },
			],
			truncated: true,
		});
		expect(
			screen.getByTestId("executions-chart-truncated"),
		).toHaveTextContent(/showing latest 1,000 runs/);
	});

	it("omits the truncation annotation for complete windows", () => {
		renderCard({
			executions: [
				{ status: "Success", started_at: new Date().toISOString() },
			],
		});
		expect(
			screen.queryByTestId("executions-chart-truncated"),
		).not.toBeInTheDocument();
	});
});
