import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { WorkerCapacityCard } from "./WorkerCapacityCard";

const bounds = {
	protocol_version: 1,
	scope: "test-scope",
	worker_version: "test-build",
	memory_limit_bytes: 4096 * 1024 * 1024,
	max_workers: 2,
	max_lifetime_seconds: 900,
};

const configured = {
	configured: true,
	endpoint: "https://capacity.example.invalid",
	bounds,
	policy_mismatches: [],
	workers: { reserved: 0, starting: 1, ready: 2, draining: 0, lost: 0, retired: 1 },
};

describe("WorkerCapacityCard", () => {
	it("shows a loading state", () => {
		render(
			<WorkerCapacityCard
				capacity={null}
				loading={true}
				loadFailed={false}
				kubernetesAvailable={true}
				onRetry={() => {}}
			/>,
		);
		expect(screen.getByRole("status")).toHaveTextContent("Loading…");
	});

	it("shows a retry state when the fetch fails", async () => {
		const user = userEvent.setup();
		const onRetry = vi.fn();
		render(
			<WorkerCapacityCard
				capacity={null}
				loading={false}
				loadFailed={true}
				kubernetesAvailable={true}
				onRetry={onRetry}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Retry" }));
		expect(onRetry).toHaveBeenCalledTimes(1);
	});

	it("notes when no external provider is configured", () => {
		render(
			<WorkerCapacityCard
				capacity={{ configured: false }}
				loading={false}
				loadFailed={false}
				kubernetesAvailable={true}
				onRetry={() => {}}
			/>,
		);
		expect(
			screen.getByText(
				/No external provider is configured, so workers scale on Kubernetes only/,
			),
		).toBeVisible();
		expect(
			screen.getByText(
				/Extra execution capacity beyond Kubernetes/,
			),
		).toBeVisible();
	});

	it("names the host fallback when Kubernetes is unavailable", () => {
		render(
			<WorkerCapacityCard
				capacity={{ configured: false }}
				loading={false}
				loadFailed={false}
				kubernetesAvailable={false}
				onRetry={() => {}}
			/>,
		);
		expect(
			screen.getByText(/Workers scale on this host without Kubernetes/),
		).toBeVisible();
	});

	it("shows bounds, live worker states, and policy mismatches", () => {
		render(
			<WorkerCapacityCard
				capacity={{
					...configured,
					policy_mismatches: [
						"worker_memory_mib must match the capacity provider's fixed worker memory (4096 MiB)",
					],
				}}
				loading={false}
				loadFailed={false}
				kubernetesAvailable={true}
				onRetry={() => {}}
			/>,
		);
		expect(screen.getByText("test-scope")).toBeVisible();
		expect(screen.getByText("test-build")).toBeVisible();
		expect(screen.getByText("4096 MiB")).toBeVisible();
		expect(screen.getByText("15 minutes")).toBeVisible();
		expect(
			screen.getByText("Active provider scope: test-scope."),
		).toBeVisible();
		expect(screen.getByText("Ready")).toBeVisible();
		expect(screen.getByText("Starting")).toBeVisible();
		expect(screen.queryByText("Draining")).not.toBeInTheDocument();
		expect(
			screen.getByText(/Scaling policy does not fit this provider/),
		).toBeVisible();
		expect(
			screen.getByText(/must match the capacity provider's fixed worker memory/),
		).toBeVisible();
	});

	it("reports a provider error with a retry", async () => {
		const user = userEvent.setup();
		const onRetry = vi.fn();
		render(
			<WorkerCapacityCard
				capacity={{
					configured: true,
					endpoint: "https://capacity.example.invalid",
					error: "Capacity provider is unreachable",
				}}
				loading={false}
				loadFailed={false}
				kubernetesAvailable={true}
				onRetry={onRetry}
			/>,
		);
		expect(
			screen.getByText("Capacity provider is unreachable"),
		).toBeVisible();
		await user.click(screen.getByRole("button", { name: "Retry" }));
		expect(onRetry).toHaveBeenCalledTimes(1);
	});

	it("shows the last reconcile result and recent fencing", () => {
		render(
			<WorkerCapacityCard
				capacity={{
					...configured,
					last_pass: {
						finished_at: new Date(
							Date.now() - 90 * 1000,
						).toISOString(),
						ok: false,
						error: "Capacity provider is unreachable",
					},
					recent_losses: [
						{ reason: "provider_resource_stopped", count: 3 },
					],
				}}
				loading={false}
				loadFailed={false}
				kubernetesAvailable={true}
				onRetry={() => {}}
			/>,
		);
		expect(
			screen.getByText(
				/Last reconcile failed 1m ago: Capacity provider is unreachable/,
			),
		).toBeVisible();
		expect(
			screen.getByText(/provider_resource_stopped.*× 3/),
		).toBeVisible();
	});

	it("shows a healthy last reconcile", () => {
		render(
			<WorkerCapacityCard
				capacity={{
					...configured,
					last_pass: {
						finished_at: new Date(
							Date.now() - 20 * 1000,
						).toISOString(),
						ok: true,
						error: null,
					},
					recent_losses: [],
				}}
				loading={false}
				loadFailed={false}
				kubernetesAvailable={true}
				onRetry={() => {}}
			/>,
		);
		expect(
			screen.getByText(/Last reconcile just now.+healthy/),
		).toBeVisible();
	});
});
