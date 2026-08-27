import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: true }),
}));

vi.mock("@/services/events", () => ({
	useEventSources: () => ({
		data: {
			items: [
				{
					id: "source-1",
					name: "Mailbox changes",
					source_type: "webhook",
					organization_id: "org-1",
					organization_name: "Covi, Inc.",
					is_active: true,
					error_message: "Provider subscription renewal failed",
					event_count_24h: 4,
					created_at: "2026-08-27T00:00:00Z",
					webhook: {
						adapter_name: "microsoft_graph",
						external_id: "subscription-1",
						expires_at: "2030-08-30T00:00:00Z",
						rate_limited_count_24h: 0,
						config: {
							resource: "/users/user-1/messages",
							change_types: ["created"],
						},
						provider_metadata: {
							user_display_name: "Ada Lovelace",
						},
					},
				},
			],
			total: 1,
		},
		isLoading: false,
		refetch: vi.fn(),
	}),
	useUpdateEventSource: () => ({ mutateAsync: vi.fn(), isPending: false }),
	useDeleteEventSource: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));

vi.mock("@/components/events/CreateEventSourceDialog", () => ({
	CreateEventSourceDialog: () => null,
}));

vi.mock("@/components/events/EditEventSourceDialog", () => ({
	EditEventSourceDialog: () => null,
}));

vi.mock("@/components/events/EventSourceDetail", () => ({
	EventSourceDetail: () => null,
}));

import { Events } from "./Events";

describe("Event sources list", () => {
	it("identifies Microsoft Graph sources and their operational context", () => {
		renderWithProviders(<Events />);

		expect(screen.getByText("Mailbox changes")).toBeInTheDocument();
		expect(screen.getByText("Microsoft Graph")).toBeInTheDocument();
		expect(
			screen.getByText(/Ada Lovelace · Mail messages · created/i),
		).toBeInTheDocument();
		expect(screen.getByText("Needs attention")).toBeInTheDocument();
	});
});
