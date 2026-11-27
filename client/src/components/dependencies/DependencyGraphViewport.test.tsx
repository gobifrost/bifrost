import { render } from "@testing-library/react";
import { expect,it,vi } from "vitest";
import { DependencyGraphViewport } from "./DependencyGraphViewport";

const data = vi.hoisted(() => ({
	width: 300,
	height: 500,
	initialized: true,
	nodes: [
		{
			id: "root",
			position: { x: 0, y: 0 },
			measured: { width: 200, height: 100 },
			data: { isRoot: true },
		},
	] as Array<{
		id: string;
		position: { x: number; y: number };
		measured?: { width: number; height: number };
		width?: number;
		height?: number;
		data?: Record<string, unknown>;
	}>,
	fitView: vi.fn(),
	setCenter: vi.fn(),
}));

vi.mock("@xyflow/react", () => ({
	useNodesStore: (selector: (state: typeof data) => unknown) => selector(data),
	useViewportStore: (selector: (state: typeof data) => unknown) => selector(data),
	useNodesInitialized: () => data.initialized,
	useReactFlow: () => ({ fitView: data.fitView, setCenter: data.setCenter }),
}));

beforeEach(() => {
	data.width = 300;
	data.height = 500;
	data.initialized = true;
	data.nodes = [
		{
			id: "root",
			position: { x: 0, y: 0 },
			measured: { width: 200, height: 100 },
			data: { isRoot: true },
		},
	];
	data.fitView.mockClear();
	data.setCenter.mockClear();
});

it("fits readable graph data once without overriding user zoom on resize-only changes", () => {
	const { rerender } = render(<DependencyGraphViewport />);

	expect(data.fitView).toHaveBeenCalledTimes(1);
	expect(data.setCenter).not.toHaveBeenCalled();

	rerender(<DependencyGraphViewport />);
	expect(data.fitView).toHaveBeenCalledTimes(1);

	data.height = 300;
	rerender(<DependencyGraphViewport />);
	expect(data.fitView).toHaveBeenCalledTimes(1);

	data.nodes = [...data.nodes, { ...data.nodes[0], id: "child" }];
	rerender(<DependencyGraphViewport />);
	expect(data.fitView).toHaveBeenCalledTimes(2);
	expect(data.fitView).toHaveBeenLastCalledWith({
		padding: "8.34%",
		maxZoom: 1.5,
		duration: 0,
	});
	data.height = 0;
	data.nodes = [...data.nodes, { ...data.nodes[0], id: "hidden" }];
	rerender(<DependencyGraphViewport />);
	expect(data.fitView).toHaveBeenCalledTimes(2);
});

it("centers the root at readable zoom when fitting the whole graph would be too small", () => {
	data.width = 900;
	data.height = 600;
	data.nodes = [
		{
			id: "root",
			position: { x: 100, y: 200 },
			measured: { width: 200, height: 100 },
			data: { isRoot: true },
		},
		...Array.from({ length: 18 }, (_, index) => ({
			id: `form-${index}`,
			position: { x: index * 260, y: 500 },
			measured: { width: 200, height: 100 },
			data: { isRoot: false },
		})),
	];

	render(<DependencyGraphViewport />);

	expect(data.fitView).not.toHaveBeenCalled();
	expect(data.setCenter).toHaveBeenCalledWith(200, 250, {
		zoom: 0.8,
		duration: 0,
	});
});

it("reframes when the root changes without changing the node set", () => {
	data.width = 1000;
	const { rerender } = render(<DependencyGraphViewport />);
	expect(data.fitView).toHaveBeenCalledTimes(1);

	data.nodes = [
		{
			...data.nodes[0],
			data: { isRoot: false },
		},
		{
			id: "child",
			position: { x: 300, y: 0 },
			measured: { width: 200, height: 100 },
			data: { isRoot: true },
		},
	];
	rerender(<DependencyGraphViewport />);
	expect(data.fitView).toHaveBeenCalledTimes(2);

	data.nodes = [
		{
			...data.nodes[0],
			data: { isRoot: true },
		},
		{
			...data.nodes[1],
			data: { isRoot: false },
		},
	];
	rerender(<DependencyGraphViewport />);
	expect(data.fitView).toHaveBeenCalledTimes(3);
});
