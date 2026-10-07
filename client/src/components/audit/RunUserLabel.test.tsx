import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { AuditLogEntry } from "@/hooks/useAuditLog";

import { RunUserLabel } from "./RunUserLabel";

function check(
	runUserFacts: Record<string, unknown> | null,
	actor: Partial<AuditLogEntry["actor"]> = {},
): AuditLogEntry {
	return {
		id: "event-1",
		timestamp: "2026-10-05T12:00:00Z",
		action: "access.check",
		resource_type: "entry",
		resource_id: null,
		outcome: "failure",
		source: "http",
		operation_id: null,
		surface: null,
		execution_id: null,
		actor: {
			user_id: "identity-1",
			user_email: null,
			user_name: "Default Identity",
			organization_id: "org-2",
			organization_name: "Fabrikam",
			...actor,
		},
		ip_address: null,
		user_agent: null,
		details: runUserFacts && {
			trace: {
				outcome: "failure",
				enforced: false,
				steps: [
					{
						key: "run_user",
						label: "Run user",
						status: "passed",
						reason: "identity",
						facts: runUserFacts,
					},
				],
			},
		},
	};
}

const names = (id: string) => ({ "org-1": "Contoso" })[id];

describe("RunUserLabel", () => {
	it("names an identity with its home organization and the hexagon glyph", () => {
		render(
			<RunUserLabel
				entry={check({
					identity_kind: "default",
					home_organization_id: "org-1",
				})}
				organizationName={names}
			/>,
		);

		expect(screen.getByRole("img", { name: "Identity" })).toBeVisible();
		expect(screen.getByText("Default Identity · Contoso")).toBeVisible();
	});

	it("falls back to the event's organization when the home has no name here", () => {
		render(
			<RunUserLabel
				entry={check({
					identity_kind: "default",
					home_organization_id: "org-9",
				})}
				organizationName={names}
			/>,
		);

		expect(screen.getByText("Default Identity · Fabrikam")).toBeVisible();
	});

	it("names a Global identity", () => {
		render(
			<RunUserLabel
				entry={check({
					identity_kind: "service",
					home_organization_id: null,
				})}
				organizationName={names}
			/>,
		);

		expect(screen.getByText("Default Identity · Global")).toBeVisible();
	});

	it("shows a person by email, without the glyph", () => {
		render(
			<RunUserLabel
				entry={check(
					{ identity_kind: null, home_organization_id: "org-1" },
					{ user_email: "avery@contoso.example", user_name: "Avery" },
				)}
				organizationName={names}
			/>,
		);

		expect(screen.getByText("avery@contoso.example")).toBeVisible();
		expect(screen.queryByRole("img", { name: "Identity" })).toBeNull();
	});

	it("shows the source when no one signed in", () => {
		const entry = check(null, { user_name: null });
		render(
			<RunUserLabel
				entry={{ ...entry, source: "scheduler" }}
				organizationName={names}
			/>,
		);

		expect(screen.getByText("(scheduler)")).toBeVisible();
	});
});
