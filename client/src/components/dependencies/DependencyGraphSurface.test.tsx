import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { DependencyGraphSurface } from "./DependencyGraphSurface";
it("distinguishes failure from empty data and preserves the graph during refresh", async () => {
	const user = userEvent.setup();
	const onRetry = vi.fn();
	const { rerender } = render(
		<DependencyGraphSurface
			graphData={null}
			isLoading={false}
			isError
			onRetry={onRetry}
		/>,
	);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Couldn’t load dependencies",
	);
	expect(screen.queryByText("No dependencies found")).not.toBeInTheDocument();
	await user.click(
		screen.getByRole("button", { name: "Retry dependencies" }),
	);
	expect(onRetry).toHaveBeenCalledTimes(1);
	const graphData = {
		root_id: "root",
		nodes: [
			{ id: "root", name: "Example workflow", type: "workflow" as const },
		],
		edges: [],
	};
	rerender(
		<DependencyGraphSurface
			graphData={graphData}
			isLoading={false}
			isError
			isFetching
			onRetry={onRetry}
		/>,
	);
	expect(screen.getByRole("toolbar", { name: "Graph view" })).toBeVisible();
	expect(screen.getByText("Example workflow")).toBeInTheDocument();
	expect(screen.getByRole("status")).toHaveTextContent(
		"Refreshing dependencies",
	);
	expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
	rerender(
		<DependencyGraphSurface graphData={graphData} isLoading={false} />,
	);
	expect(screen.queryByRole("alert")).not.toBeInTheDocument();
	expect(screen.getByRole("toolbar", { name: "Graph view" })).toBeVisible();
});
