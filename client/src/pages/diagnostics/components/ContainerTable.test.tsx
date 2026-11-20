import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
vi.mock("./MemoryChart", () => ({ CONTAINER_COLORS: ["var(--primary)"] }));
vi.mock("./ForkTable", () => ({
	ForkTable: ({ workerId, actionsEnabled }: { workerId: string; actionsEnabled?: boolean }) => (
		<div>
			Processes for {workerId}
			<button type="button" disabled={actionsEnabled === false}>Recycle All</button>
		</div>
	),
}));
import { ContainerTable } from "./ContainerTable";
const pool = {
	worker_id: "fixture-worker",
	hostname: null,
	status: "online",
	started_at: null,
	last_heartbeat: null,
	requirements_installed: null,
	requirements_total: null,
	processes: [
		{
			process_id: "fixture-process",
			pid: 12,
			state: "idle" as const,
			current_execution_id: null,
			executions_completed: 3,
			started_at: null,
			uptime_seconds: 20,
			memory_mb: 10,
			is_alive: true,
		},
	],
};
it("exposes labelled container metrics and an operable process disclosure", async () => {
	const user = userEvent.setup();
	render(<ContainerTable pools={[pool]} workerIds={[pool.worker_id]} />);
	const article = screen.getByRole("article", {
		name: "Container fixture-worker",
	});
	expect(article).toHaveTextContent("1 total · 0 busy · 1 idle");
	const details = article.querySelector("details")!;
	expect(details).not.toHaveAttribute("open");
	await user.click(screen.getByText("View processes"));
	expect(details).toHaveAttribute("open");
	expect(screen.getByText("Processes for fixture-worker")).toBeVisible();
});
it("explains missing process details instead of an inert expanded row", async () => {
	const user = userEvent.setup();
	render(
		<ContainerTable
			pools={[{ ...pool, processes: [] }]}
			workerIds={[pool.worker_id]}
		/>,
	);
	await user.click(screen.getByText("View processes"));
	expect(
		screen.getByText(
			"Process details are not available for this container.",
		),
	).toBeVisible();
});

it("shows canonical lifecycle history and disables process controls before readiness", async () => {
	render(
		<ContainerTable
			pools={[
				{
					...pool,
					incarnation: {
						worker_id: "fixture-worker",
						form: "external",
						state: "starting",
						execution_lane: "normal",
						version: "2026.11",
						capacity_scope: "cloudflare:primary",
						created_at: "2026-11-14T12:00:00Z",
						ready_at: null,
						ended_at: null,
						end_reason: null,
						memory_budget_bytes: null,
						memory_reserved_bytes: 0,
						cleanup_confirmed: false,
					},
				}
			]}
			workerIds={[pool.worker_id]}
		/>,
	);
	expect(screen.getByText("external · normal")).toBeVisible();
	expect(screen.getByText("cloudflare:primary")).toBeVisible();

	await userEvent.setup().click(screen.getByText("View processes"));
	expect(screen.getByRole("button", { name: "Recycle All" })).toBeDisabled();
});
