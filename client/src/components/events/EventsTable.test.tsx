/**
 * Component tests for EventsTable.
 *
 * Focus: filter + empty-state UX. Real-time + routing side effects are
 * elided by stubbing useEventStream and useEvents. The child
 * EventDetailDialog is rendered but is effectively invisible until the user
 * navigates into an event, so we don't exercise it here.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const useEventsMock = vi.fn();

vi.mock("@/services/events", async () => {
	const actual = await vi.importActual<typeof import("@/services/events")>(
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
	EventDetailDialog: () => <div data-marker="event-detail" />,
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
	it("shows the 'No Events Yet' state", () => {
		useEventsMock.mockReturnValue({ data: { items: [] }, isLoading: false });
		renderWithProviders(<EventsTable sourceId="src-1" />);
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

		expect(screen.getByText("ticket.created")).toBeInTheDocument();
		expect(screen.getByText("10.0.0.1")).toBeInTheDocument();
		// Not connected by default
		expect(screen.queryByText("Live")).not.toBeInTheDocument();
	});

	it("filters client-side by search term on event type", () => {
		useEventsMock.mockReturnValue({
			data: {
				items: [
					makeEvent({ id: "e1", event_type: "ticket.created" }),
					makeEvent({ id: "e2", event_type: "ticket.updated" }),
				],
			},
			isLoading: false,
		});
		const { container } = renderWithProviders(<EventsTable sourceId="src-1" />);
		const search = container.querySelector(
			"input[placeholder='Search events...']",
		) as HTMLInputElement;
		expect(search).toBeTruthy();

		// Before filtering, both rows are present
		expect(screen.getByText("ticket.created")).toBeInTheDocument();
		expect(screen.getByText("ticket.updated")).toBeInTheDocument();
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

		expect(screen.getByText("graph.messages.created")).toBeInTheDocument();
		expect(
			screen.queryByText("01V6T7ZK0M0Q8SHJ4A1N5W2X9B.created"),
		).not.toBeInTheDocument();
	});
});
