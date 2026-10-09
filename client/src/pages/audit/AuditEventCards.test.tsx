import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import type { AuditLogEntry } from "@/hooks/useAuditLog";

import { AuditEventCards } from "./AuditEventCards";

const entry: AuditLogEntry = {
	id: "event-1",
	timestamp: "2026-10-05T12:00:00Z",
	action: "role.update",
	resource_type: "role",
	resource_id: null,
	outcome: "success",
	source: "http",
	operation_id: null,
	surface: null,
	execution_id: null,
	actor: {
		user_id: "user-1",
		user_email: "avery@contoso.example",
		user_name: "Avery",
		organization_id: null,
		organization_name: null,
	},
	ip_address: null,
	user_agent: null,
	details: null,
};

describe("AuditEventCards", () => {
	it("opens a card's event from its action, by keyboard", async () => {
		const onOpen = vi.fn();
		const { user } = renderWithProviders(
			<AuditEventCards
				entries={[entry]}
				context={() => "-"}
				onOpen={onOpen}
			/>,
		);

		const open = screen.getByRole("button", { name: "role.update" });
		open.focus();
		await user.keyboard(" ");

		expect(onOpen).toHaveBeenCalledWith(entry, open);
	});
});
