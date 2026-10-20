/**
 * Component tests for DeliveriesTable.
 *
 * Covers:
 *   - empty state message when there are no deliveries
 *   - Retry button is platform-admin-only and dispatches the retry mutation
 *   - Send button is only visible for not_delivered subscriptions
 *   - workflow vs agent target rendering
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockRetry = vi.fn();
const mockCreateDelivery = vi.fn();
let mockIsPlatformAdmin = true;

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: mockIsPlatformAdmin }),
}));

vi.mock("@/services/events", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/events")>(
			"@/services/events",
		);
	return {
		...actual,
		useRetryDelivery: () => ({
			mutateAsync: mockRetry,
			isPending: false,
		}),
		useCreateDelivery: () => ({
			mutateAsync: mockCreateDelivery,
			isPending: false,
		}),
	};
});

import { DeliveriesTable } from "./DeliveriesTable";
import type { EventDelivery } from "@/services/events";

function makeDelivery(overrides: Partial<EventDelivery> = {}): EventDelivery {
	return {
		id: "del-1",
		event_id: "evt-1",
		event_subscription_id: "sub-1",
		workflow_id: "wf-1",
		workflow_name: "Onboard Ticket",
		status: "success",
		attempt_count: 1,
		completed_at: "2026-04-20T12:00:00Z",
		execution_id: "exec-1",
		...overrides,
	} as EventDelivery;
}

beforeEach(() => {
	mockRetry.mockReset();
	mockRetry.mockResolvedValue(undefined);
	mockCreateDelivery.mockReset();
	mockCreateDelivery.mockResolvedValue(undefined);
	mockIsPlatformAdmin = true;
});

describe("DeliveriesTable — empty state", () => {
	it("renders the empty-state copy", () => {
		renderWithProviders(
			<DeliveriesTable deliveries={[]} eventId="evt-1" />,
		);
		expect(
			screen.getByText(/no deliveries for this event/i),
		).toBeInTheDocument();
	});
});

describe("DeliveriesTable — retry", () => {
	it("dispatches retry for failed deliveries (admin only)", async () => {
		const { user } = renderWithProviders(
			<DeliveriesTable
				canManage
				deliveries={[
					makeDelivery({
						status: "failed",
						error_message: "timeout",
					}),
				]}
				eventId="evt-1"
			/>,
		);

		await user.click(screen.getByRole("button", { name: /retry/i }));

		await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(1));
		expect(mockRetry.mock.calls[0]![0].params.path.delivery_id).toBe(
			"del-1",
		);
	});

	it("hides the retry button for non-admin viewers", () => {
		mockIsPlatformAdmin = false;
		renderWithProviders(
			<DeliveriesTable
				deliveries={[
					makeDelivery({
						status: "failed",
						error_message: "timeout",
					}),
				]}
				eventId="evt-1"
			/>,
		);
		expect(
			screen.queryByRole("button", { name: /retry/i }),
		).not.toBeInTheDocument();
	});
});

describe("DeliveriesTable — not_delivered", () => {
	it("shows a Send button for not_delivered and dispatches createDelivery", async () => {
		const { user } = renderWithProviders(
			<DeliveriesTable
				canManage
				deliveries={[
					makeDelivery({
						id: undefined,
						status: "not_delivered" as EventDelivery["status"],
					}),
				]}
				eventId="evt-1"
			/>,
		);

		await user.click(screen.getByRole("button", { name: /^send$/i }));

		await waitFor(() =>
			expect(mockCreateDelivery).toHaveBeenCalledTimes(1),
		);
		expect(mockCreateDelivery.mock.calls[0]![0].body.subscription_id).toBe(
			"sub-1",
		);
	});

	it("renders 'Subscription added after this event arrived' copy", () => {
		renderWithProviders(
			<DeliveriesTable
				deliveries={[
					makeDelivery({
						id: undefined,
						status: "not_delivered" as EventDelivery["status"],
					}),
				]}
				eventId="evt-1"
			/>,
		);
		expect(
			screen.getByText(/subscription added after this event arrived/i),
		).toBeInTheDocument();
	});
});

describe("DeliveriesTable — agent deliveries", () => {
	it("renders the agent name when target_type=agent", () => {
		renderWithProviders(
			<DeliveriesTable
				deliveries={[
					{
						...makeDelivery({
							workflow_id: null as unknown as string,
							workflow_name: null as unknown as string,
						}),
						target_type: "agent",
						agent_id: "agent-1",
						agent_name: "Triage Bot",
						agent_run_id: "run-1",
					} as unknown as EventDelivery,
				]}
				eventId="evt-1"
			/>,
		);
		expect(screen.getByText(/triage bot/i)).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: /view agent run/i }),
		).toHaveAttribute("href", "/agents/agent-1/runs/run-1");
	});
});

describe("DeliveriesTable — send permissions", () => {
	it("hides Send for non-admin viewers even when an event ID exists", () => {
		mockIsPlatformAdmin = false;
		renderWithProviders(
			<DeliveriesTable
				deliveries={[makeDelivery({ status: "not_delivered" })]}
				eventId="evt-1"
			/>,
		);
		expect(
			screen.queryByRole("button", { name: "Send" }),
		).not.toBeInTheDocument();
	});
});

describe("DeliveriesTable — recovery", () => {
	it("keeps a failed retry available and reports the failure beside the record", async () => {
		mockRetry.mockRejectedValueOnce(new Error("offline"));
		const { user } = renderWithProviders(
			<DeliveriesTable
				canManage
				deliveries={[makeDelivery({ status: "failed" })]}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Retry" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not queue the retry",
		);
		await user.click(screen.getByRole("button", { name: "Retry" }));
		await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(2));
		await waitFor(() =>
			expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
		);
	});
	it("reports clipboard failure and copies the full visible error on retry", async () => {
		const { user } = renderWithProviders(
			<DeliveriesTable
				deliveries={[
					makeDelivery({
						status: "failed",
						error_message: "Full failure details",
					}),
				]}
			/>,
		);
		const copy = vi
			.spyOn(navigator.clipboard, "writeText")
			.mockRejectedValueOnce(new Error("denied"))
			.mockResolvedValue(undefined);
		await user.click(screen.getByRole("button", { name: "Copy error" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not copy",
		);
		await user.click(screen.getByRole("button", { name: "Copy error" }));
		expect(copy).toHaveBeenLastCalledWith("Full failure details");
		await waitFor(() =>
			expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
		);
		copy.mockRestore();
	});
	it("does not offer Send when the event ID is unavailable", () => {
		renderWithProviders(
			<DeliveriesTable
				deliveries={[makeDelivery({ status: "not_delivered" })]}
			/>,
		);
		expect(
			screen.queryByRole("button", { name: "Send" }),
		).not.toBeInTheDocument();
	});
});
