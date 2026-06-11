import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DashboardStatCards } from "./DashboardStatCards";
import { summarizeOutcomes } from "@/lib/execution-buckets";

const INVENTORY = { workflows: 8, forms: 4, agents: 3, apps: 2 };
const ROI = { timeSavedMinutes: 95, value: 1234.5, valueUnit: "USD" };

function renderCards(
	overrides: Partial<
		React.ComponentProps<typeof DashboardStatCards>
	> = {},
) {
	const props = {
		windowLabel: "Last 7 days",
		outcomes: summarizeOutcomes([
			...Array.from({ length: 9 }, () => ({ status: "Success" })),
			...Array.from({ length: 3 }, () => ({ status: "Failed" })),
		]),
		truncated: false,
		executionsLoading: false,
		executionsError: false,
		inventory: INVENTORY,
		inventoryLoading: false,
		roi: ROI,
		roiLoading: false,
		...overrides,
	};
	return render(
		<MemoryRouter>
			<DashboardStatCards {...props} />
		</MemoryRouter>,
	);
}

describe("DashboardStatCards", () => {
	it("shows success rate and execution count for the chart window", () => {
		renderCards();
		expect(screen.getByText("75.0%")).toBeInTheDocument();
		expect(screen.getByText("12")).toBeInTheDocument();
		expect(screen.getAllByText("Last 7 days")).toHaveLength(2);
	});

	it("annotates the window label when the fetch was truncated", () => {
		renderCards({ truncated: true });
		expect(
			screen.getAllByText("Last 7 days · latest 1,000 runs"),
		).toHaveLength(2);
		expect(screen.queryByText("Last 7 days")).not.toBeInTheDocument();
	});

	it("shows skeletons while the executions window is loading", () => {
		const { container } = renderCards({ executionsLoading: true });
		expect(
			container.querySelectorAll('[data-slot="skeleton"]').length,
		).toBeGreaterThanOrEqual(2);
	});

	it("shows a dash when the executions fetch failed or there are no runs", () => {
		renderCards({
			executionsError: true,
			outcomes: summarizeOutcomes([]),
		});
		expect(screen.getAllByText("—")).toHaveLength(2);
	});

	it("links each inventory count to its section", () => {
		renderCards();
		expect(
			screen.getByRole("link", { name: /Workflows\s*8/ }),
		).toHaveAttribute("href", "/workflows");
		expect(screen.getByRole("link", { name: /Forms\s*4/ })).toHaveAttribute(
			"href",
			"/forms",
		);
		expect(
			screen.getByRole("link", { name: /Agents\s*3/ }),
		).toHaveAttribute("href", "/agents");
		expect(screen.getByRole("link", { name: /Apps\s*2/ })).toHaveAttribute(
			"href",
			"/apps",
		);
	});

	it("shows inventory skeletons while counts load", () => {
		renderCards({ inventoryLoading: true });
		const card = screen.getByTestId("inventory-card");
		expect(
			card.querySelectorAll('[data-slot="skeleton"]'),
		).toHaveLength(4);
	});

	it("combines time saved and value in one card with a ROI link", () => {
		renderCards();
		expect(screen.getByText("1h 35m")).toBeInTheDocument();
		expect(screen.getByText("1,234.5")).toBeInTheDocument();
		expect(screen.getByText("USD")).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: /Value \(24h\)/ }),
		).toHaveAttribute("href", "/reports/roi");
	});

	it("renders zeroes when ROI data is absent", () => {
		renderCards({ roi: undefined });
		expect(screen.getByText("0m")).toBeInTheDocument();
		expect(screen.getByText("0")).toBeInTheDocument();
	});
});
