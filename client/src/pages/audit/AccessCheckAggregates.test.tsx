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
			<AccessCheckAggregates
				groups={groups}
				desktop
				renderChecks={() => null}
			/>,
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
			within(row).getByRole("button", { name: "Show 8 Recorded Checks" }),
		).toHaveAttribute("aria-expanded", "false");
	});

	it("loads only an opened summary and permits closing it with the keyboard", async () => {
		const renderChecks = vi.fn((group: AuditLogGroup) => (
			<p>Original {group.key}</p>
		));
		const { user } = renderWithProviders(
			<AccessCheckAggregates
				groups={groups}
				desktop
				renderChecks={renderChecks}
			/>,
		);
		expect(renderChecks).not.toHaveBeenCalled();
		const button = screen.getByRole("button", {
			name: "Show 8 Recorded Checks",
		});
		button.focus();
		await user.keyboard("{Enter}");
		expect(button).toHaveAttribute("aria-expanded", "true");
		expect(
			screen.getByRole("region", { name: "Recorded Checks" }),
		).toHaveTextContent("Original first-window");
		expect(renderChecks).toHaveBeenLastCalledWith(groups[0]);
		await user.keyboard("{Enter}");
		expect(
			screen.queryByRole("region", { name: "Recorded Checks" }),
		).not.toBeInTheDocument();
		expect(button).toHaveFocus();
	});

	it("opens one summary at a time", async () => {
		const { user } = renderWithProviders(
			<AccessCheckAggregates
				groups={groups}
				desktop
				renderChecks={(group) => <p>Original {group.key}</p>}
			/>,
		);
		await user.click(
			screen.getByRole("button", { name: "Show 8 Recorded Checks" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Show 2 Recorded Checks" }),
		);
		expect(
			screen.getAllByRole("region", { name: "Recorded Checks" }),
		).toHaveLength(1);
		expect(
			screen.getByRole("region", { name: "Recorded Checks" }),
		).toHaveTextContent("Original second-window");
		expect(
			screen.getByRole("button", { name: "Show 8 Recorded Checks" }),
		).toHaveAttribute("aria-expanded", "false");
	});

	it("keeps a summary open when its newest recorded check changes", async () => {
		const renderChecks = (group: AuditLogGroup) => <p>{group.sample.id}</p>;
		const { user, rerender } = renderWithProviders(
			<AccessCheckAggregates
				groups={[groups[0]]}
				desktop
				renderChecks={renderChecks}
			/>,
		);
		await user.click(
			screen.getByRole("button", { name: "Show 8 Recorded Checks" }),
		);
		rerender(
			<AccessCheckAggregates
				groups={[
					{
						...groups[0],
						count: 9,
						sample: { ...entry, id: "newest-check" },
					},
				]}
				desktop
				renderChecks={renderChecks}
			/>,
		);
		expect(
			screen.getByRole("button", { name: "Hide 9 Recorded Checks" }),
		).toHaveAttribute("aria-expanded", "true");
		expect(
			screen.getByRole("region", { name: "Recorded Checks" }),
		).toHaveTextContent("newest-check");
	});

	it("keeps organization first and the same disclosure on phones", async () => {
		const { user } = renderWithProviders(
			<AccessCheckAggregates
				groups={[groups[0]]}
				desktop={false}
				renderChecks={() => <p>Original Checks</p>}
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
				name: "Show 8 Recorded Checks",
			}),
		);
		expect(
			screen.getByRole("region", { name: "Recorded Checks" }),
		).toHaveTextContent("Original Checks");
	});
});
