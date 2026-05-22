import { describe, expect, it } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ContainerTable } from "./ContainerTable";
import type { PoolSummary } from "@/services/workers";

function pool(worker_id: string, hostname?: string): PoolSummary {
	return {
		worker_id,
		hostname: hostname ?? worker_id,
		status: "online",
		started_at: new Date(Date.now() - 60_000).toISOString(),
		pool_size: 2,
		idle_count: 2,
		busy_count: 0,
		last_heartbeat: new Date().toISOString(),
		requirements_installed: null,
		requirements_total: null,
	};
}

describe("ContainerTable", () => {
	it("labels worker runtime origin from worker identifiers", () => {
		const pools = [
			pool("28f617b0f52a"),
			pool("bifrost-worker-aks-next-5b977ccc5b-l4jpz"),
			pool("bifrost-worker-aca-next--0000003"),
		];

		renderWithProviders(
			<ContainerTable
				pools={pools}
				workerIds={pools.map((item) => item.worker_id)}
			/>,
		);

		expect(screen.getByText("VM")).toBeInTheDocument();
		expect(screen.getByText("AKS pod")).toBeInTheDocument();
		expect(screen.getByText("ACA app")).toBeInTheDocument();
	});
});
