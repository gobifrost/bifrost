import { describe, expect, it, vi } from "vitest";
import { within } from "@testing-library/react";

import type { AuditLogEntry, AuditLogGroup } from "@/hooks/useAuditLog";
import { renderWithProviders, screen } from "@/test-utils";

import { AccessCheckAggregates } from "./AccessCheckAggregates";

const entry: AuditLogEntry = {
	id: "check-1",
	timestamp: "2026-10-16T12:30:00Z",
	action: "access.check",
	resource_type: "permission",
	resource_id: null,
	outcome: "failure",
	source: "http",
	operation_id: "workflows.execute",
	surface: "workflow",
	execution_id: null,
	actor: {
		user_id: "user-1",
		user_email: "operator@contoso.example",
		user_name: "Contoso Operator",
		organization_id: "org-1",
		organization_name: "Fabrikam",
		home_organization_id: "org-2",
		home_organization_name: "Contoso",
	},
	workflow_name: "Nightly Sync",
	ip_address: null,
	user_agent: null,
	details: null,
};

const groups: AuditLogGroup[] = [
	{
		key: "first-window",
		count: 8,
		last_seen: entry.timestamp,
		first_seen: "2026-10-16T12:10:00Z",
		window_start: "2026-10-16T12:00:00Z",
		window_end: "2026-10-16T13:00:00Z",
		sample: entry,
	},
	{
		key: "second-window",
		count: 2,
		last_seen: "2026-10-16T11:30:00Z",
		first_seen: "2026-10-16T11:20:00Z",
		window_start: "2026-10-16T11:00:00Z",
		window_end: "2026-10-16T12:00:00Z",
		sample: { ...entry, id: "check-2" },
	},
];

describe("AccessCheckAggregates", () => {
	it("leads with organization and shows the recorded count and decision context", () => {
		renderWithProviders(
			<AccessCheckAggregates groups={groups} desktop onOpen={vi.fn()} />,
		);
		expect(
			screen.getAllByRole("columnheader").map((cell) => cell.textContent),
		).toEqual([
			"Organization",
			"Last Seen",
			"Run User",
			"Resource Type",
			"# Checks",
		]);
		const row = screen.getAllByRole("row")[1];
		expect(within(row).getAllByRole("cell")[0]).toHaveTextContent(
			"Fabrikam",
		);
		expect(row).toHaveTextContent("operator@contoso.example");
		expect(row).toHaveTextContent("Nightly Sync");
		expect(row).toHaveTextContent("workflows.execute");
		expect(
			within(row).getByRole("button", { name: "Open 8 Recorded Checks" }),
		).toHaveAttribute("aria-haspopup", "dialog");
	});

	it("opens the inspector with the keyboard without inserting another table", async () => {
		const onOpen = vi.fn();
		const { user } = renderWithProviders(
			<AccessCheckAggregates groups={groups} desktop onOpen={onOpen} />,
		);
		expect(onOpen).not.toHaveBeenCalled();
		const button = screen.getByRole("button", {
			name: "Open 8 Recorded Checks",
		});
		button.focus();
		await user.keyboard("{Enter}");
		expect(onOpen).toHaveBeenCalledExactlyOnceWith(groups[0], button);
		expect(screen.getAllByRole("table")).toHaveLength(1);
	});

	it("opens a clicked row and invokes its button only once", async () => {
		const onOpen = vi.fn();
		const { user } = renderWithProviders(
			<AccessCheckAggregates groups={groups} desktop onOpen={onOpen} />,
		);
		const row = screen.getAllByRole("row")[1];
		await user.click(within(row).getAllByRole("cell")[0]);
		expect(onOpen).toHaveBeenCalledExactlyOnceWith(
			groups[0],
			within(row).getByRole("button"),
		);
		onOpen.mockClear();
		await user.click(
			screen.getByRole("button", { name: "Open 2 Recorded Checks" }),
		);
		expect(onOpen).toHaveBeenCalledExactlyOnceWith(
			groups[1],
			screen.getByRole("button", { name: "Open 2 Recorded Checks" }),
		);
	});

	it("keeps organization first and opens the inspector on phones", async () => {
		const onOpen = vi.fn();
		const { user } = renderWithProviders(
			<AccessCheckAggregates
				groups={[groups[0]]}
				desktop={false}
				onOpen={onOpen}
			/>,
		);
		const record = within(
			screen.getByRole("list", { name: "Check Summaries" }),
		).getByRole("listitem");
		const terms = within(record).getAllByRole("term");
		expect(terms[0]).toHaveTextContent("Organization");
		expect(terms[0].nextElementSibling).toHaveTextContent("Fabrikam");
		await user.click(
			within(record).getByRole("button", {
				name: "Open 8 Recorded Checks",
			}),
		);
		expect(onOpen).toHaveBeenCalledExactlyOnceWith(
			groups[0],
			within(record).getByRole("button"),
		);
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
	});
});
