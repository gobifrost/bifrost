import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect,it,vi } from "vitest";
import { DependencyGraphControls } from "./DependencyGraphControls";

const actions = vi.hoisted(() => ({
	zoomIn: vi.fn(),
	zoomOut: vi.fn(),
	fitView: vi.fn(),
	store: {
		transform: [0, 0, 1],
		nodes: [] as Array<{ id: string; selected?: boolean }>,
	},
}));

vi.mock("@xyflow/react", () => ({
	useReactFlow: () => actions,
	useNodesStore: (selector: (state: typeof actions.store) => unknown) =>
		selector(actions.store),
	useViewportStore: (selector: (state: typeof actions.store) => unknown) =>
		selector(actions.store),
}));

beforeEach(() => {
	actions.zoomIn.mockClear();
	actions.zoomOut.mockClear();
	actions.fitView.mockClear();
	actions.store.transform = [0, 0, 1];
	actions.store.nodes = [];
});

it("exposes distinct keyboard-operable zoom and fit actions", async () => {
	const user = userEvent.setup();
	render(<DependencyGraphControls />);

	expect(screen.getByText("100%")).toBeInTheDocument();

	await user.click(screen.getByRole("button", { name: "Zoom in" }));
	expect(actions.zoomIn).toHaveBeenCalledWith({ duration: 0 });

	await user.click(screen.getByRole("button", { name: "Zoom out" }));
	expect(actions.zoomOut).toHaveBeenCalledWith({ duration: 0 });

	await user.click(screen.getByRole("button", { name: "Fit graph" }));
	expect(actions.fitView).toHaveBeenCalledWith({
		padding: "8.34%",
		maxZoom: 1.5,
		duration: 0,
	});
});

it("focuses the selected nodes when a selection exists", async () => {
	const user = userEvent.setup();
	actions.store.nodes = [
		{ id: "a" },
		{ id: "b", selected: true },
		{ id: "c", selected: true },
	];

	render(<DependencyGraphControls />);

	await user.click(
		screen.getByRole("button", { name: "Focus 2 selected nodes" }),
	);

	expect(actions.fitView).toHaveBeenLastCalledWith({
		nodes: actions.store.nodes.filter((node) => node.selected),
		padding: "9.68%",
		maxZoom: 1.5,
		duration: 0,
	});
});
