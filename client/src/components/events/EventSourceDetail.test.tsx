/**
 * Component tests for EventSourceDetail.
 *
 * Covers the loading / not-found / populated branches plus the toggle-active
 * and delete-confirmation wiring. Child tables (Subscriptions / Events) and
 * the edit dialog are stubbed — they have their own specs.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const useEventSourceMock = vi.fn();
const mockDelete = vi.fn();
const mockUpdate = vi.fn();
const mockResubscribe = vi.fn();
const mockRotate = vi.fn();
const mockRotateReset = vi.fn();
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
		useEventSource: (...args: unknown[]) => useEventSourceMock(...args),
		useDeleteEventSource: () => ({
			mutateAsync: mockDelete,
			isPending: false,
		}),
		useUpdateEventSource: () => ({
			mutateAsync: mockUpdate,
			isPending: false,
		}),
		useResubscribeEventSource: () => ({
			mutateAsync: mockResubscribe,
			isPending: false,
		}),
		useRotateWebhookSecret: () => ({
			mutateAsync: mockRotate,
			reset: mockRotateReset,
			isPending: false,
		}),
	};
});

vi.mock("./SubscriptionsTable", () => ({
	SubscriptionsTable: () => <div data-marker="subs-table" />,
}));

vi.mock("./EventsTable", () => ({
	EventsTable: () => <div data-marker="events-table" />,
}));

vi.mock("./EditEventSourceDialog", () => ({
	EditEventSourceDialog: () => <div data-marker="edit-dlg" />,
}));

import { EventSourceDetail } from "./EventSourceDetail";
import type { EventSource } from "@/services/events";

function makeSource(overrides: Partial<EventSource> = {}): EventSource {
	return {
		id: "src-1",
		name: "GitHub Hooks",
		source_type: "webhook",
		organization_id: null,
		is_active: true,
		subscription_count: 2,
		event_count_24h: 5,
		webhook: {
			adapter_name: "github",
			callback_url: "/api/events/sources/src-1/webhook",
		},
		...overrides,
	} as unknown as EventSource;
}

beforeEach(() => {
	mockDelete.mockReset();
	mockDelete.mockResolvedValue(undefined);
	mockUpdate.mockReset();
	mockUpdate.mockResolvedValue(undefined);
	mockResubscribe.mockReset();
	mockResubscribe.mockResolvedValue(undefined);
	mockRotate.mockReset();
	mockRotateReset.mockReset();
	useEventSourceMock.mockReset();
	mockIsPlatformAdmin = true;
});

describe("EventSourceDetail — empty/error branches", () => {
	it("shows skeleton while loading", () => {
		useEventSourceMock.mockReturnValue({
			data: undefined,
			isLoading: true,
			refetch: vi.fn(),
		});
		const { container } = renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);
		expect(container.querySelector(".animate-pulse")).toBeTruthy();
	});

	it("shows the not-found state when the source load returns null", async () => {
		useEventSourceMock.mockReturnValue({
			data: null,
			isLoading: false,
			refetch: vi.fn(),
		});
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={onClose} />,
		);
		expect(screen.getByText(/event source not found/i)).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: /back to event sources/i }),
		);
		expect(onClose).toHaveBeenCalled();
	});
});

describe("EventSourceDetail — populated", () => {
	it("renders the source name, metadata badges, and Global label", () => {
		useEventSourceMock.mockReturnValue({
			data: makeSource(),
			isLoading: false,
			refetch: vi.fn(),
		});
		renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);

		expect(
			screen.getByRole("heading", { name: /github hooks/i }),
		).toBeInTheDocument();
		expect(screen.getByText(/2 subscriptions/i)).toBeInTheDocument();
		expect(screen.getByText(/5 events \(24h\)/i)).toBeInTheDocument();
		expect(screen.getByText(/global/i)).toBeInTheDocument();
	});

	it("toggles active via the switch (platform admin)", async () => {
		useEventSourceMock.mockReturnValue({
			data: makeSource(),
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);

		await user.click(screen.getByRole("switch"));

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		expect(mockUpdate.mock.calls[0]![0].body).toEqual({ is_active: false });
	});

	it("asks to confirm deletion and dispatches the mutation on confirm", async () => {
		useEventSourceMock.mockReturnValue({
			data: makeSource(),
			isLoading: false,
			refetch: vi.fn(),
		});
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={onClose} />,
		);

		await user.click(screen.getByRole("button", { name: / actions$/ }));
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));

		expect(
			screen.getByRole("heading", { name: /delete event source/i }),
		).toBeInTheDocument();
		expect(mockDelete).not.toHaveBeenCalled();

		await user.click(screen.getByRole("button", { name: /^delete$/i }));

		await waitFor(() => expect(mockDelete).toHaveBeenCalledTimes(1));
		expect(onClose).toHaveBeenCalled();
	});

	it("rotates a generic webhook's signing secret and reveals it once", async () => {
		mockRotate.mockResolvedValue({ raw_secret: "synthetic-rotated-value" });
		useEventSourceMock.mockReturnValue({
			data: makeSource({
				webhook: {
					adapter_name: "generic",
					callback_url: "/api/hooks/src-1",
					secret_set: true,
				},
			} as Partial<EventSource>),
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);

		await user.click(screen.getByRole("button", { name: / actions$/ }));
		await user.click(
			screen.getByRole("menuitem", { name: "Rotate signing secret" }),
		);
		expect(mockRotate).not.toHaveBeenCalled();
		await user.click(screen.getByRole("button", { name: "Rotate secret" }));

		await waitFor(() => expect(mockRotate).toHaveBeenCalledTimes(1));
		expect(mockRotate.mock.calls[0]![0]).toEqual({
			params: { path: { source_id: "src-1" } },
			body: {},
		});
		expect(mockRotateReset).toHaveBeenCalled();
		expect(
			screen.getByRole("region", { name: "New webhook signing secret" }),
		).toHaveTextContent("synthetic-rotated-value");

		await user.click(screen.getByRole("button", { name: "Dismiss secret" }));
		expect(
			screen.queryByText("synthetic-rotated-value"),
		).not.toBeInTheDocument();
	});

	it("offers no secret rotation for adapters without a signing secret", async () => {
		useEventSourceMock.mockReturnValue({
			data: makeSource(),
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);

		await user.click(screen.getByRole("button", { name: / actions$/ }));
		expect(
			screen.queryByRole("menuitem", { name: "Rotate signing secret" }),
		).not.toBeInTheDocument();
	});

	it("hides admin-only controls for non-admin viewers", () => {
		mockIsPlatformAdmin = false;
		useEventSourceMock.mockReturnValue({
			data: makeSource(),
			isLoading: false,
			refetch: vi.fn(),
		});
		renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);
		expect(screen.queryByRole("switch")).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: / actions$/ }),
		).not.toBeInTheDocument();
	});

	it("shows Graph identity and recreates the provider subscription", async () => {
		useEventSourceMock.mockReturnValue({
			data: makeSource({
				name: "Mailbox changes",
				organization_id: "org-1",
				organization_name: "Covi, Inc.",
				webhook: {
					adapter_name: "microsoft_graph",
					callback_url: "/api/hooks/src-1",
					external_id: "graph-subscription-1",
					expires_at: "2030-08-30T00:00:00Z",
					rate_limit_per_minute: 60,
					rate_limit_window_seconds: 60,
					rate_limit_enabled: true,
					rate_limited_count_24h: 0,
					secret_set: false,
					config: {
						resource: "/users/user-1/messages",
						change_types: ["created"],
					},
					provider_metadata: {
						user_display_name: "Ada Lovelace",
						user_principal_name: "ada@example.com",
					},
				},
			}),
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);

		expect(
			screen.getByRole("heading", { name: "Microsoft Graph" }),
		).toBeInTheDocument();
		expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
		expect(screen.getByText("Mail messages")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Resubscribe" }));
		expect(
			screen.getByRole("heading", {
				name: /resubscribe to microsoft graph/i,
			}),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Resubscribe" }));

		await waitFor(() => expect(mockResubscribe).toHaveBeenCalledTimes(1));
		expect(mockResubscribe.mock.calls[0]![0]).toEqual({
			params: { path: { source_id: "src-1" } },
		});
	});
});

describe("EventSourceDetail — read recovery", () => {
	it("offers retry for a failed read without claiming the source was deleted", async () => {
		const refetch = vi.fn();
		useEventSourceMock.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			refetch,
		});
		const { user } = renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);
		expect(screen.getByRole("alert")).toHaveTextContent("Could not load");
		expect(
			screen.queryByText(/may have been deleted/),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Retry" }));
		expect(refetch).toHaveBeenCalledTimes(1);
	});
	it("keeps cached details visible when refresh fails", () => {
		useEventSourceMock.mockReturnValue({
			data: makeSource(),
			isLoading: false,
			isError: true,
			refetch: vi.fn(),
		});
		renderWithProviders(
			<EventSourceDetail sourceId="src-1" onClose={() => {}} />,
		);
		expect(
			screen.getByRole("heading", { name: "GitHub Hooks" }),
		).toBeInTheDocument();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Showing the last loaded details",
		);
	});
});
