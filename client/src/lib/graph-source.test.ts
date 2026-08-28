import { describe, expect, it } from "vitest";

import type { EventSource } from "@/services/events";
import {
	getGraphEventType,
	getGraphSourceSummary,
	isMicrosoftGraphSource,
} from "./graph-source";
import type { Event } from "@/services/events";

function graphSource(overrides: Partial<EventSource> = {}): EventSource {
	return {
		id: "source-1",
		name: "Mailbox changes",
		source_type: "webhook",
		is_active: true,
		error_message: null,
		webhook: {
			adapter_name: "microsoft_graph",
			external_id: "subscription-1",
			expires_at: "2026-08-30T00:00:00Z",
			config: {
				user_id: "user-1",
				resource: "/users/user-1/messages",
				change_types: ["created", "updated"],
			},
			provider_metadata: {
				user_display_name: "Ada Lovelace",
				user_principal_name: "ada@example.com",
			},
		},
		...overrides,
	} as unknown as EventSource;
}

describe("Graph source presentation", () => {
	it("recognizes Graph sources and returns operator-friendly identity", () => {
		const source = graphSource();

		expect(isMicrosoftGraphSource(source)).toBe(true);
		expect(
			getGraphSourceSummary(source, new Date("2026-08-29T00:00:00Z")),
		).toEqual({
			userLabel: "ada@example.com",
			userSecondary: "Ada Lovelace",
			resourceLabel: "Mail messages",
			resourcePath: "/users/user-1/messages",
			changeLabel: "created, updated",
			health: "connected",
		});
	});

	it("normalizes historical Graph event types from source and payload data", () => {
		const event = {
			event_type: "01V6T7ZK0M0Q8SHJ4A1N5W2X9B.created",
			data: { change_type: "created" },
		} as unknown as Event;

		expect(getGraphEventType(graphSource(), event)).toBe(
			"graph.messages.created",
		);
	});

	it("marks missing or expired provider registrations for attention", () => {
		expect(
			getGraphSourceSummary(
				graphSource({
					webhook: {
						...graphSource().webhook!,
						external_id: null,
					},
				}),
				new Date("2026-08-29T00:00:00Z"),
			)?.health,
		).toBe("attention");
		expect(
			getGraphSourceSummary(
				graphSource(),
				new Date("2026-09-01T00:00:00Z"),
			)?.health,
		).toBe("expired");
	});

	it("does not manufacture Graph metadata for generic webhooks", () => {
		const source = graphSource({
			webhook: { ...graphSource().webhook!, adapter_name: "generic" },
		});

		expect(isMicrosoftGraphSource(source)).toBe(false);
		expect(getGraphSourceSummary(source)).toBeNull();
	});
});
