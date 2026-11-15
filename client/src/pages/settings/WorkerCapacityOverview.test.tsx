import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	usePools: vi.fn(),
	refetch: vi.fn(),
	onPoolMessage: vi.fn(),
	unsubscribe: vi.fn(),
}));

vi.mock("@/services/workers", () => ({
	usePools: (...args: unknown[]) => mocks.usePools(...args),
}));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		onPoolMessage: (...args: unknown[]) => mocks.onPoolMessage(...args),
	},
}));

import { WorkerCapacityOverview } from "./WorkerCapacityOverview";

const capacityOverview = {
	global_managed_limit: 8,
	managed_in_use: 3,
	cloudflare_profile_limit: 4,
	cleanup_pending_count: 2,
	forms: [
		{ form: "static" as const, active: 1, reserved: 0, starting: 1, ready: 2, draining: 0 },
		{ form: "job" as const, active: 0, reserved: 1, starting: 0, ready: 3, draining: 1 },
		{ form: "external" as const, active: 2, reserved: 0, starting: 2, ready: 1, draining: 0 },
	],
};

describe("WorkerCapacityOverview", () => {
	beforeEach(() => {
		mocks.usePools.mockReset();
		mocks.refetch.mockReset();
		mocks.onPoolMessage.mockReset();
		mocks.unsubscribe.mockReset();
		mocks.onPoolMessage.mockReturnValue(mocks.unsubscribe);
		mocks.usePools.mockReturnValue({
			data: { pools: [], total: 0, capacity_overview: capacityOverview },
			isLoading: false,
			isError: false,
			refetch: mocks.refetch,
			dataUpdatedAt: Date.now(),
		});
	});

	it("shows canonical managed capacity and per-form readiness", () => {
		render(<WorkerCapacityOverview />);
		expect(screen.getByText("Managed Workers")).toBeVisible();
		expect(screen.getByText("3 / 8")).toBeVisible();
		expect(screen.getByText("Cloudflare Profile Bound")).toBeVisible();
		expect(screen.getByText("4")).toBeVisible();
		expect(screen.getByText("Local")).toBeVisible();
		expect(screen.getByText("Kubernetes")).toBeVisible();
		expect(screen.getByText("External")).toBeVisible();
		expect(screen.getByRole("status")).toHaveTextContent(
			"2 resources are pending cleanup; removal is not yet confirmed.",
		);
	});

	it("refreshes on demand and only reacts to worker lifecycle messages", async () => {
		const user = userEvent.setup();
		let listener: ((message: { type: string }) => void) | undefined;
		mocks.onPoolMessage.mockImplementation((callback) => {
			listener = callback;
			return mocks.unsubscribe;
		});
		const { unmount } = render(<WorkerCapacityOverview />);
		await user.click(
			screen.getByRole("button", { name: "Refresh Capacity Overview" }),
		);
		expect(mocks.refetch).toHaveBeenCalledOnce();
		listener?.({ type: "worker_heartbeat" });
		expect(mocks.refetch).toHaveBeenCalledOnce();
		listener?.({ type: "worker_online" });
		expect(mocks.refetch).toHaveBeenCalledTimes(2);
		unmount();
		expect(mocks.unsubscribe).toHaveBeenCalledOnce();
	});

	it("keeps the card compact when the snapshot has no overview", () => {
		mocks.usePools.mockReturnValue({
			data: { pools: [], total: 0, capacity_overview: null },
			isLoading: false,
			isError: false,
			refetch: mocks.refetch,
			dataUpdatedAt: 0,
		});
		render(<WorkerCapacityOverview />);
		expect(screen.getByRole("status")).toHaveTextContent(
			"Capacity overview is not available for this snapshot.",
		);
	});
});
