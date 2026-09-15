import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { Route, Routes } from "react-router-dom";
import { waitFor } from "@testing-library/react";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";

const useEventSourcesMock = vi.fn();
const useMediaQueryMock = vi.fn();
const useSearchMock = vi.fn();
const useAuthMock = vi.fn();
const deleteMutationMock = {
	mutateAsync: vi.fn(),
	isPending: false,
};

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => useAuthMock(),
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: (...args: unknown[]) => useMediaQueryMock(...args),
}));

vi.mock("@/hooks/useSearch", () => ({
	useSearch: (...args: unknown[]) => useSearchMock(...args),
}));

vi.mock("@/services/events", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/events")>(
			"@/services/events",
		);
	return {
		...actual,
		useEventSources: (...args: unknown[]) => useEventSourcesMock(...args),
		useUpdateEventSource: () => ({
			mutateAsync: vi.fn().mockResolvedValue(undefined),
			isPending: false,
		}),
		useDeleteEventSource: () => deleteMutationMock,
	};
});

vi.mock("@/components/events/CreateEventSourceDialog", () => ({
	CreateEventSourceDialog: () => <div data-marker="create-dialog" />,
}));

vi.mock("@/components/events/EditEventSourceDialog", () => ({
	EditEventSourceDialog: () => <div data-marker="edit-dialog" />,
}));

vi.mock("@/components/events/EventSourceDetail", () => ({
	EventSourceDetail: () => <div data-testid="event-source-detail" />,
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => <div data-marker="organization-select" />,
}));

import { Events } from "./Events";

function makeSource(overrides = {}) {
	return {
		id: "source-1",
		name: "Synthetic event source with a long unbroken name for mobile review",
		source_type: "webhook",
		organization_id: "org-1",
		organization_name: "Northwind Automation",
		is_active: true,
		subscription_count: 2,
		event_count_24h: 12,
		created_at: "2026-09-06T12:00:00Z",
		webhook: {
			adapter_name: "github",
			callback_url: "/api/events/sources/source-1/webhook",
			rate_limited_count_24h: 1,
		},
		...overrides,
	};
}

beforeEach(() => {
	vi.useFakeTimers({ toFake: ["Date"] });
	vi.setSystemTime(new Date("2026-09-07T12:00:00Z"));
	useEventSourcesMock.mockReset();
	useMediaQueryMock.mockReset();
	useSearchMock.mockReset();
	useAuthMock.mockReset();
	deleteMutationMock.mutateAsync.mockReset();
	deleteMutationMock.isPending = false;
	useAuthMock.mockReturnValue({ isPlatformAdmin: true });
	useSearchMock.mockImplementation((items: unknown[]) => items);
	useEventSourcesMock.mockReturnValue({
		data: { items: [makeSource()], total: 1 },
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
});

afterEach(() => {
	vi.useRealTimers();
});

describe("Events page", () => {
	it("renders mobile source cards instead of the desktop table", () => {
		useMediaQueryMock.mockReturnValue(true);

		renderWithProviders(<Events />, {
			initialEntries: ["/event-sources"],
		});

		expect(
			screen.getByRole("heading", { name: "Event Sources" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("link", {
				name: /synthetic event source with a long unbroken name/i,
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole("switch", {
				name: /toggle synthetic event source/i,
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /create event source/i }),
		).toBeInTheDocument();
		expect(screen.getByText(/1 day ago/i)).toBeInTheDocument();
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
	});

	it("renders the desktop table on wide screens", () => {
		useMediaQueryMock.mockReturnValue(false);

		renderWithProviders(<Events />, {
			initialEntries: ["/event-sources"],
		});

		expect(screen.getByRole("table")).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: /synthetic event source/i }),
		).toHaveAttribute("href", "/event-sources/source-1");
		expect(
			screen.getByRole("cell", { name: /northwind automation/i }),
		).toBeInTheDocument();
	});

	it("opens an event source row href on ctrl-click from a plain cell", () => {
		const open = vi.spyOn(window, "open").mockImplementation(() => null);
		useMediaQueryMock.mockReturnValue(false);

		renderWithProviders(<Events />, {
			initialEntries: ["/event-sources"],
		});

		fireEvent.click(screen.getByRole("cell", { name: /northwind automation/i }), {
			ctrlKey: true,
		});

		expect(open).toHaveBeenCalledWith("/event-sources/source-1", "_blank");
	});

	it("routes to the detail shell when a source id is present", () => {
		useMediaQueryMock.mockReturnValue(true);

		renderWithProviders(
			<Routes>
				<Route path="/event-sources/:sourceId" element={<Events />} />
			</Routes>,
			{
				initialEntries: ["/event-sources/source-1"],
			},
		);

		expect(screen.getByTestId("event-source-detail")).toBeInTheDocument();
	});

	it("shows a retryable read error without hiding cached sources", () => {
		const refetch = vi.fn();
		useMediaQueryMock.mockReturnValue(true);
		useEventSourcesMock.mockReturnValue({
			data: { items: [makeSource()], total: 1 },
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch,
		});

		renderWithProviders(<Events />, {
			initialEntries: ["/event-sources"],
		});

		expect(screen.getByRole("alert")).toHaveTextContent(
			"Previously loaded records are shown below.",
		);
		expect(
			screen.getByRole("link", { name: /synthetic event source/i }),
		).toBeInTheDocument();
	});

	it("offers retry when the initial list read fails", async () => {
		const refetch = vi.fn();
		useMediaQueryMock.mockReturnValue(true);
		useEventSourcesMock.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch,
		});

		const { user } = renderWithProviders(<Events />, {
			initialEntries: ["/event-sources"],
		});

		await user.click(
			screen.getByRole("button", { name: /retry loading/i }),
		);
		expect(refetch).toHaveBeenCalledTimes(1);
		expect(screen.queryByText(/no event sources/i)).not.toBeInTheDocument();
	});

	it("keeps the delete dialog open and surfaces a failure for retry", async () => {
		useMediaQueryMock.mockReturnValue(true);
		deleteMutationMock.mutateAsync
			.mockRejectedValueOnce(new Error("provider offline"))
			.mockResolvedValueOnce(undefined);

		const { user } = renderWithProviders(<Events />, {
			initialEntries: ["/event-sources"],
		});

		await user.click(screen.getByRole("button", { name: / actions$/i }));
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		await user.click(screen.getByRole("button", { name: /^delete$/i }));

		await waitFor(() =>
			expect(screen.getByRole("alert")).toHaveTextContent(
				"provider offline",
			),
		);
		expect(screen.getByRole("button", { name: /^delete$/i })).toBeEnabled();
		await user.click(screen.getByRole("button", { name: /^delete$/i }));
		await waitFor(() =>
			expect(deleteMutationMock.mutateAsync).toHaveBeenCalledTimes(2),
		);
	});
});
