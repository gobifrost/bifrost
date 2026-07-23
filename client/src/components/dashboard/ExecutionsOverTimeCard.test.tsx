import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExecutionsOverTimeCard } from "./ExecutionsOverTimeCard";

function renderCard(
	overrides: Partial<
		React.ComponentProps<typeof ExecutionsOverTimeCard>
	> = {},
) {
	const props = {
		window: "7d" as const,
		onWindowChange: vi.fn(),
		buckets: [],
		outcomes: { success: 0, failed: 0, total: 0, successRate: null },
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
			buckets: undefined,
		});
		expect(
			container.querySelector('[data-slot="skeleton"]'),
		).toBeInTheDocument();
		expect(
			screen.queryByTestId("executions-chart-empty"),
		).not.toBeInTheDocument();
	});

	it("shows an error state when the fetch failed", () => {
		renderCard({ isError: true, buckets: undefined });
		expect(
			screen.getByTestId("executions-chart-error"),
		).toBeInTheDocument();
	});

	it("shows an empty state when the window has no terminal runs", () => {
		renderCard();
		expect(
			screen.getByTestId("executions-chart-empty"),
		).toBeInTheDocument();
		expect(
			screen.getByText("No executions in this window"),
		).toBeInTheDocument();
	});

	it("summarizes run totals and links the failed count to filtered history", () => {
		renderCard({
			buckets: [
				{
					start: new Date().toISOString(),
					success_count: 2,
					failed_count: 1,
				},
			],
			outcomes: { success: 2, failed: 1, total: 3, successRate: 2 / 3 },
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
			buckets: [
				{
					start: new Date().toISOString(),
					success_count: 1,
					failed_count: 0,
				},
			],
			outcomes: { success: 1, failed: 0, total: 1, successRate: 100 },
		});
		expect(screen.queryByRole("link")).not.toBeInTheDocument();
	});

	it("invokes onWindowChange when a window toggle is clicked", () => {
		const { props } = renderCard();
		fireEvent.click(screen.getByRole("radio", { name: "Last 24 hours" }));
		expect(props.onWindowChange).toHaveBeenCalledWith("24h");
	});

	it("renders a complete 30-day aggregate without a truncation warning", () => {
		renderCard({
			window: "30d",
			buckets: Array.from({ length: 30 }, (_, index) => ({
				start: new Date(2026, 5, index + 1).toISOString(),
				success_count: index + 1,
				failed_count: 0,
			})),
			outcomes: { success: 465, failed: 0, total: 465, successRate: 100 },
		});
		expect(screen.getByText(/Last 30 days · 465 runs/)).toBeInTheDocument();
		expect(screen.queryByText(/latest 1,000/)).not.toBeInTheDocument();
	});
});
