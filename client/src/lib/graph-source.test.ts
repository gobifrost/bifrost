import { describe, expect, it } from "vitest";

import type { EventSource } from "@/services/events";
import {
	getGraphSourceSummary,
	isMicrosoftGraphSource,
} from "./graph-source";

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
			userLabel: "Ada Lovelace",
			userSecondary: "ada@example.com",
			resourceLabel: "Mail messages",
			resourcePath: "/users/user-1/messages",
			changeLabel: "created, updated",
			health: "connected",
		});
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
