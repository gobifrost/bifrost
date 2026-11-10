import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { WorkerMetricPoint } from "@/services/workers";
import { MemoryChart } from "./MemoryChart";

const metrics = vi.hoisted(() => ({
	points: [] as WorkerMetricPoint[],
	chart: [] as unknown[],
}));
vi.mock("@/services/workers", () => ({
	useWorkerMetrics: () => ({
		data: { points: metrics.points },
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));
vi.mock("recharts", () => ({
	ResponsiveContainer: ({ children }: { children: ReactNode }) => (
		<div>{children}</div>
	),
	AreaChart: ({
		data,
		children,
	}: {
		data: unknown[];
		children: ReactNode;
	}) => {
		metrics.chart = data;
		return <div>{children}</div>;
	},
	Area: () => null,
	XAxis: () => null,
	YAxis: () => null,
	CartesianGrid: () => null,
	Tooltip: () => null,
	ReferenceLine: () => null,
}));
const point = (
	bucket_start: string,
	memory_current: number,
	worker_id = "a",
): WorkerMetricPoint => ({
	bucket_start,
	group: "Nov 10",
	memory_current,
	worker_id,
	memory_max: 1024,
	fork_count: 0,
	busy_count: 0,
	idle_count: 0,
});
beforeEach(() => {
	metrics.points = [];
	metrics.chart = [];
});

it("keeps exact buckets separate despite matching labels and replaces repeated worker samples", () => {
	metrics.points = [
		point("2026-11-10T01:00:00Z", 100),
		point("2026-11-10T01:30:00Z", 200),
	];
	render(
		<MemoryChart
			livePoints={[
				point("2026-11-10T01:30:00Z", 250),
				point("2026-11-10T01:30:00Z", 50, "b"),
			]}
		/>,
	);
	expect(metrics.chart).toEqual([
		{ group: "Nov 10", total: 100 },
		{ group: "Nov 10", total: 300 },
	]);
});

it("leaves unknown historical samples as gaps and does not claim zero current usage", () => {
	metrics.points = [
		point("2026-11-10T01:00:00Z", 100),
		point("2026-11-10T01:30:00Z", -1),
	];
	render(<MemoryChart />);
	expect(metrics.chart).toEqual([
		{ group: "Nov 10", total: 100 },
		{ group: "Nov 10", total: null },
	]);
	expect(
		screen.getByText("Memory usage is unavailable for one or more containers."),
	).toBeVisible();
	expect(screen.queryByText("0 MB")).not.toBeInTheDocument();
});

it("does not turn an unknown live pool into zero usage", () => {
	render(
		<MemoryChart
			livePools={[
				{
					worker_id: "a",
					hostname: null,
					status: null,
					started_at: null,
					last_heartbeat: null,
					processes: [],
					requirements_installed: null,
					requirements_total: null,
					memory_current_bytes: -1,
					memory_max_bytes: -1,
				},
			]}
		/>,
	);
	expect(
		screen.getByText("Memory usage is unavailable for one or more containers."),
	).toBeVisible();
});
