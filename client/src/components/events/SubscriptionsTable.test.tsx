/**
 * Component tests for SubscriptionsTable.
 *
 * Covers the empty/populated branches and the active-toggle wiring. Delete
 * confirmation dispatches the mutation; we verify both the confirm dialog
 * and the downstream call.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockDelete = vi.fn();
const mockUpdate = vi.fn();
const useSubsMock = vi.fn();

vi.mock("@/services/events", async () => {
	const actual = await vi.importActual<typeof import("@/services/events")>(
		"@/services/events",
	);
	return {
		...actual,
		useSubscriptions: (...args: unknown[]) => useSubsMock(...args),
		useDeleteSubscription: () => ({
			mutateAsync: mockDelete,
			isPending: false,
		}),
		useUpdateSubscription: () => ({
			mutateAsync: mockUpdate,
			isPending: false,
		}),
	};
});

vi.mock("./CreateSubscriptionDialog", () => ({
	CreateSubscriptionDialog: () => <div data-testid="create-sub-dialog" />,
}));

vi.mock("./EditSubscriptionDialog", () => ({
	EditSubscriptionDialog: () => <div data-testid="edit-sub-dialog" />,
}));

import { SubscriptionsTable } from "./SubscriptionsTable";
import type { EventSubscription } from "@/services/events";

function makeSub(overrides: Partial<EventSubscription> = {}): EventSubscription {
	return {
		id: "sub-1",
		source_id: "src-1",
		workflow_id: "wf-1",
		workflow_name: "Onboard",
		event_type: null,
		input_mapping: null,
		is_active: true,
		delivery_count: 10,
		success_count: 9,
		failed_count: 1,
		...overrides,
	} as unknown as EventSubscription;
}

beforeEach(() => {
	mockDelete.mockReset();
	mockDelete.mockResolvedValue(undefined);
	mockUpdate.mockReset();
	mockUpdate.mockResolvedValue(undefined);
	useSubsMock.mockReset();
});

describe("SubscriptionsTable — empty", () => {
	it("shows the empty state with an Add button", () => {
		useSubsMock.mockReturnValue({
			data: { items: [] },
			isLoading: false,
			refetch: vi.fn(),
		});
		renderWithProviders(<SubscriptionsTable sourceId="src-1" canManage />);
		expect(screen.getByText(/no subscriptions/i)).toBeInTheDocument();
		expect(
			screen.getAllByRole("button", { name: /add subscription/i }).length,
		).toBeGreaterThan(0);
	});
});

describe("SubscriptionsTable — populated", () => {
	it("renders a row with the workflow name and success rate", () => {
		useSubsMock.mockReturnValue({
			data: { items: [makeSub()] },
			isLoading: false,
			refetch: vi.fn(),
		});
		renderWithProviders(<SubscriptionsTable sourceId="src-1" canManage />);

		expect(screen.getAllByText("Onboard")[0]).toBeInTheDocument();
		expect(screen.getAllByText("90%")[0]).toBeInTheDocument();
		expect(
			screen.getAllByRole("button", { name: /edit subscription for onboard/i })[0],
		).toHaveClass("min-h-11");
		expect(
			screen.getAllByRole("button", { name: /onboard actions/i })[0],
		).toHaveClass("size-11");
	});

	it("toggles the active switch and dispatches the update", async () => {
		useSubsMock.mockReturnValue({
			data: { items: [makeSub()] },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SubscriptionsTable sourceId="src-1" canManage />,
		);

		await user.click(screen.getAllByRole("switch")[0]!);

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		expect(mockUpdate.mock.calls[0]![0].body).toEqual({ is_active: false });
	});

	it("opens the edit dialog from the resource name button", async () => {
		useSubsMock.mockReturnValue({
			data: { items: [makeSub()] },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SubscriptionsTable sourceId="src-1" canManage />,
		);

		await user.click(
			screen.getAllByRole("button", {
				name: /edit subscription for onboard/i,
			})[0]!,
		);

		expect(screen.getByTestId("edit-sub-dialog")).toBeInTheDocument();
	});

	it("opens edit and delete from the shared actions menu", async () => {
		useSubsMock.mockReturnValue({
			data: { items: [makeSub()] },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SubscriptionsTable sourceId="src-1" canManage />,
		);

		await user.click(
			screen.getAllByRole("button", { name: /onboard actions/i })[0]!,
		);
		await user.click(screen.getByRole("menuitem", { name: "Edit" }));

		expect(screen.getByTestId("edit-sub-dialog")).toBeInTheDocument();

		await user.click(
			screen.getAllByRole("button", { name: /onboard actions/i })[0]!,
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));

		expect(
			screen.getByRole("heading", { name: /delete subscription/i }),
		).toBeInTheDocument();
	});

	it("asks for delete confirmation before deleting", async () => {
		useSubsMock.mockReturnValue({
			data: { items: [makeSub()] },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SubscriptionsTable sourceId="src-1" canManage />,
		);

		await user.click(
			screen.getAllByRole("button", { name: /onboard actions/i })[0]!,
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));

		// Alert dialog shows before dispatch
		expect(
			screen.getByRole("heading", { name: /delete subscription/i }),
		).toBeInTheDocument();
		expect(mockDelete).not.toHaveBeenCalled();

		await user.click(screen.getByRole("button", { name: /^delete$/i }));

		await waitFor(() => expect(mockDelete).toHaveBeenCalledTimes(1));
		expect(mockDelete.mock.calls[0]![0].params.path).toEqual({
			source_id: "src-1",
			subscription_id: "sub-1",
		});
	});

	it("keeps the delete dialog open after a failed delete and retries", async () => {
		mockDelete.mockRejectedValueOnce(new Error("boom"));
		useSubsMock.mockReturnValue({
			data: { items: [makeSub()] },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<SubscriptionsTable sourceId="src-1" canManage />,
		);

		await user.click(
			screen.getAllByRole("button", { name: /onboard actions/i })[0]!,
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		await user.click(screen.getByRole("button", { name: /^delete$/i }));

		expect(
			await screen.findByRole("alert"),
		).toHaveTextContent(/could not delete this subscription/i);
		expect(
			screen.getByRole("button", { name: /retry delete/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: /delete subscription/i }),
		).toBeInTheDocument();

		mockDelete.mockResolvedValueOnce(undefined);
		await user.click(screen.getByRole("button", { name: /retry delete/i }));

		await waitFor(() => expect(mockDelete).toHaveBeenCalledTimes(2));
		expect(
			screen.queryByRole("heading", { name: /delete subscription/i }),
		).not.toBeInTheDocument();
	});
});

describe("SubscriptionsTable — read error", () => {
	it("shows retry UI when the initial read fails", async () => {
		const refetch = vi.fn();
		useSubsMock.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch,
		});

		const { user } = renderWithProviders(
			<SubscriptionsTable sourceId="src-1" canManage />,
		);

		expect(
			screen.getByRole("alert"),
		).toHaveTextContent(/could not load subscriptions/i);
		await user.click(
			screen.getByRole("button", { name: /retry subscriptions/i }),
		);
		expect(refetch).toHaveBeenCalledTimes(1);
	});

	it("keeps cached subscriptions visible when refresh fails", () => {
		useSubsMock.mockReturnValue({
			data: { items: [makeSub()] },
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch: vi.fn(),
		});

		renderWithProviders(<SubscriptionsTable sourceId="src-1" canManage />);

		expect(screen.getAllByText("Onboard")[0]).toBeInTheDocument();
		expect(
			screen.getByRole("alert"),
		).toHaveTextContent(/previously loaded subscriptions are still shown/i);
	});
});
