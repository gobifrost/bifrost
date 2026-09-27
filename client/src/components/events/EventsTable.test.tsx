/**
 * Component tests for EventsTable.
 *
 * Focus: filter + empty-state UX. Real-time + routing side effects are
 * elided by stubbing useEventStream and useEvents. The child
 * EventDetailDialog is rendered but is effectively invisible until the user
 * navigates into an event, so we don't exercise it here.
 */

import { describe, it, expect, vi } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";

const useEventsMock = vi.fn();

vi.mock("@/services/events", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/events")>(
			"@/services/events",
		);
	return {
		...actual,
		useEvents: (...args: unknown[]) => useEventsMock(...args),
	};
});

vi.mock("@/hooks/useEventStream", () => ({
	useEventStream: () => ({ isConnected: false }),
}));

vi.mock("./EventDetailDialog", () => ({
	EventDetailDialog: ({
		event,
		eventId,
	}: {
		event: { id: string } | null;
		eventId?: string;
	}) => <div data-marker="event-detail">{event?.id ?? eventId ?? ""}</div>,
}));

import { EventsTable } from "./EventsTable";
import type { Event, EventSource } from "@/services/events";

function makeEvent(overrides: Partial<Event> = {}): Event {
	return {
		id: "evt-1",
		source_id: "src-1",
		event_type: "ticket.created",
		status: "completed",
		received_at: "2026-04-20T12:00:00Z",
		source_ip: "10.0.0.1",
		delivery_count: 1,
		success_count: 1,
		failed_count: 0,
		...overrides,
	} as unknown as Event;
}

function graphSource(): EventSource {
	return {
		id: "src-1",
		name: "Mailbox changes",
		source_type: "webhook",
		webhook: {
			adapter_name: "microsoft_graph",
			config: {
				resource: "/users/user-1/messages",
			},
			provider_metadata: {},
		},
	} as unknown as EventSource;
}

describe("EventsTable — empty", () => {
	it("offers to clear the default date filter before showing all-time empty state", async () => {
		useEventsMock.mockReturnValue({
			data: { items: [] },
			isLoading: false,
		});
		const { user } = renderWithProviders(<EventsTable sourceId="src-1" />);
		expect(
			screen.getByText("No events match your filters"),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Clear filters" }));
		expect(screen.getByText(/no events yet/i)).toBeInTheDocument();
	});
});

describe("EventsTable — populated", () => {
	it("renders a row per event and surfaces the Live badge only while connected", () => {
		useEventsMock.mockReturnValue({
			data: { items: [makeEvent()] },
			isLoading: false,
		});
		renderWithProviders(<EventsTable sourceId="src-1" />);

		expect(screen.getAllByText("ticket.created")).toHaveLength(2);
		expect(screen.getAllByText("10.0.0.1")).toHaveLength(2);
		// Not connected by default
		expect(screen.queryByText("Live")).not.toBeInTheDocument();
	});

	it("opens an event row href on ctrl-click from a plain cell", () => {
		const open = vi.spyOn(window, "open").mockImplementation(() => null);
		useEventsMock.mockReturnValue({
			data: { items: [makeEvent()] },
			isLoading: false,
		});

		renderWithProviders(<EventsTable sourceId="src-1" />);

		fireEvent.click(screen.getByRole("cell", { name: "10.0.0.1" }), {
			ctrlKey: true,
		});

		expect(open).toHaveBeenCalledWith(
			"/event-sources/src-1/events/evt-1",
			"_blank",
		);
	});

	it("passes the deep-linked event id to the detail dialog when the event is outside the current list window", () => {
		useEventsMock.mockReturnValue({
			data: {
				items: [makeEvent({ id: "evt-other" })],
			},
			isLoading: false,
		});

		renderWithProviders(
			<EventsTable sourceId="src-1" initialEventId="evt-deep" />,
		);

		expect(
			screen.getByText("evt-deep", { selector: "[data-marker='event-detail']" }),
		).toBeInTheDocument();
	});

	it("filters client-side by search term on event type", async () => {
		useEventsMock.mockReturnValue({
			data: {
				items: [
					makeEvent({ id: "e1", event_type: "ticket.created" }),
					makeEvent({ id: "e2", event_type: "ticket.updated" }),
				],
			},
			isLoading: false,
		});
		const { container, user } = renderWithProviders(
			<EventsTable sourceId="src-1" />,
		);
		const search = container.querySelector(
			"input[placeholder='Search events...']",
		) as HTMLInputElement;
		expect(search).toBeTruthy();

		// Before filtering, both rows are present
		expect(screen.getAllByText("ticket.created")).toHaveLength(2);
		expect(screen.getAllByText("ticket.updated")).toHaveLength(2);
		await user.type(search, "created");
		await waitFor(() =>
			expect(
				screen.queryByText("ticket.updated"),
			).not.toBeInTheDocument(),
		);
		expect(screen.getAllByText("ticket.created")).toHaveLength(2);
	});

	it("shows a compact type for historical Graph events", () => {
		useEventsMock.mockReturnValue({
			data: {
				items: [
					makeEvent({
						event_type: "01V6T7ZK0M0Q8SHJ4A1N5W2X9B.created",
						data: { change_type: "created" },
					}),
				],
			},
			isLoading: false,
		});

		renderWithProviders(
			<EventsTable sourceId="src-1" source={graphSource()} />,
		);

		expect(screen.getAllByText("graph.messages.created")).toHaveLength(2);
		expect(
			screen.queryByText("01V6T7ZK0M0Q8SHJ4A1N5W2X9B.created"),
		).not.toBeInTheDocument();
	});
});

describe("EventsTable read recovery", () => {
	it("offers retry instead of an empty result when the initial read fails", async () => {
		const refetch = vi.fn();
		useEventsMock.mockReturnValue({
			data: undefined,
			isError: true,
			isLoading: false,
			refetch,
		});
		const { user } = renderWithProviders(<EventsTable sourceId="src-1" />);
		expect(
			screen.queryByText("No events match your filters"),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Retry events" }));
		expect(refetch).toHaveBeenCalledTimes(1);
	});
	it("retains existing records when their refresh fails", () => {
		useEventsMock.mockReturnValue({
			data: { items: [makeEvent()] },
			isError: true,
			isLoading: false,
			refetch: vi.fn(),
		});
		renderWithProviders(<EventsTable sourceId="src-1" />);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Previously loaded events remain visible",
		);
		expect(
			screen.getAllByRole("link", { name: "ticket.created" }),
		).toHaveLength(2);
	});
});
