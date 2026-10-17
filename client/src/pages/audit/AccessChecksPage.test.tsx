import { beforeEach, describe, expect, it, vi } from "vitest";
import { within } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router-dom";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import type { AuditLogEntry, AuditLogGroup } from "@/hooks/useAuditLog";
import { formatRelativeTime } from "@/lib/utils";
import type { AccessStep } from "@/services/access";

const audit = vi.hoisted(() => ({
	groups: vi.fn(),
	list: vi.fn(),
}));

vi.mock("@/hooks/useAuditLog", () => ({
	useAuditGroups: (...args: unknown[]) => audit.groups(...args),
	useAuditLog: (...args: unknown[]) => audit.list(...args),
	useAuditExplain: () => ({
		data: undefined,
		error: null,
		isFetching: false,
	}),
}));

vi.mock("@/hooks/useTraceNames", () => ({
	useTraceNames: () => ({
		names: {
			role: () => undefined,
			organization: () => undefined,
			permission: (permission: string) => permission,
		},
		organizationNames: new Map(),
	}),
}));

const lists = vi.hoisted(() => ({ readable: true }));
const media = vi.hoisted(() => ({ desktop: true }));

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => media.desktop,
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: lists.readable ? [{ id: "org-1", name: "Contoso" }] : undefined,
	}),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ canAnywhere: () => lists.readable }),
}));

import { AccessChecksPage } from "./AccessChecksPage";

function stopping(key: string, label: string): AccessStep[] {
	return [
		{
			key: "run_user",
			label: "Run user",
			status: "passed",
			reason: "identity",
			facts: { identity_kind: "default", home_organization_id: "org-1" },
		},
		{ key, label, status: "stopped", reason: "outside", facts: {} },
	];
}

function check(id: string, steps: AccessStep[]): AuditLogEntry {
	return {
		id,
		timestamp: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
		action: "access.check",
		resource_type: "entry",
		resource_id: null,
		outcome: "failure",
		source: "http",
		operation_id: "workflows.execute",
		surface: null,
		execution_id: null,
		actor: {
			user_id: "identity-1",
			user_email: null,
			user_name: "Default Identity",
			organization_id: "org-2",
			organization_name: "Fabrikam",
			home_organization_id: "org-1",
			home_organization_name: "Contoso",
		},
		workflow_name: "Nightly Sync",
		ip_address: null,
		user_agent: null,
		details: {
			workflow_id: "wf-1",
			trace: { outcome: "failure", enforced: false, steps },
		},
	};
}

const sample = check("event-1", stopping("target", "Target in reach"));
const groups: AuditLogGroup[] = [
	{ key: "wf-1", count: 12, last_seen: sample.timestamp, sample },
	{
		key: null,
		count: 3,
		last_seen: sample.timestamp,
		sample: check("event-2", stopping("permission", "Permission")),
	},
];

function query<T>(data: T, overrides: Record<string, unknown> = {}) {
	return {
		data,
		isLoading: false,
		isFetching: false,
		error: null,
		refetch: vi.fn(),
		...overrides,
	};
}

function Probe() {
	const location = useLocation();
	return <output data-testid="location">{location.search}</output>;
}

function renderPage(url = "/audit/access-checks") {
	return renderWithProviders(
		<Routes>
			<Route
				path="/audit/access-checks"
				element={
					<>
						<AccessChecksPage />
						<Probe />
					</>
				}
			/>
		</Routes>,
		{ initialEntries: [url] },
	);
}

const search = () => screen.getByTestId("location").textContent;

beforeEach(() => {
	lists.readable = true;
	media.desktop = true;
	audit.groups.mockReset();
	audit.list.mockReset();
	audit.groups.mockReturnValue(query({ entries: [], groups }));
	audit.list.mockImplementation((params: { aggregate?: string }) =>
		params.aggregate
			? query({
					entries: [],
					groups: [
						{
							...groups[0],
							key: "window-1",
							first_seen: sample.timestamp,
						},
					],
					continuation_token: null,
				})
			: query({ entries: [sample], continuation_token: null }),
	);
});

describe("AccessChecksPage", () => {
	it("lists would-deny checks grouped by workflow", () => {
		renderPage();

		expect(screen.getByRole("heading", { name: "Auditing" })).toBeVisible();
		expect(
			screen.getByRole("tab", { name: "Access Checks" }),
		).toHaveAttribute("aria-selected", "true");
		expect(audit.groups).toHaveBeenLastCalledWith("workflow", {
			action: "access.check",
			outcome: "failure",
		});
		const [, first, second] = screen.getAllByRole("row");
		expect(first).toHaveTextContent("Nightly Sync");
		expect(first).toHaveTextContent("12");
		expect(first).toHaveTextContent(formatRelativeTime(sample.timestamp));
		expect(first).toHaveTextContent("Target in Reach");
		expect(second).toHaveTextContent("No Workflow");
		expect(second).toHaveTextContent("Permission");
		expect(
			screen.getByRole("columnheader", { name: "Decision Point" }),
		).toBeVisible();
	});

	it("switches the grouping and keeps it in the URL", async () => {
		const { user } = renderPage();

		await user.click(screen.getByRole("radio", { name: "Resource Type" }));

		expect(search()).toBe("?group_by=resource_type");
		expect(audit.groups).toHaveBeenLastCalledWith("resource_type", {
			action: "access.check",
			outcome: "failure",
		});
		expect(
			screen.getByRole("columnheader", { name: "Resource Type" }),
		).toBeVisible();
	});

	it("says when no check would have been denied", () => {
		audit.groups.mockReturnValue(query({ entries: [], groups: [] }));
		renderPage();

		expect(screen.getByText("No Failed Access Checks")).toBeVisible();
		expect(
			screen.getByText(
				"No denied or report-only failed checks match these filters.",
			),
		).toBeVisible();
	});

	it("shows loading, then an error with a retry", async () => {
		audit.groups.mockReturnValue(query(undefined, { isLoading: true }));
		const { rerender, user } = renderPage();
		expect(
			screen.getByRole("status", { name: "Loading access checks" }),
		).toBeVisible();

		const refetch = vi.fn();
		audit.groups.mockReturnValue(
			query(undefined, {
				error: { detail: "Database unavailable" },
				refetch,
			}),
		);
		rerender(
			<Routes>
				<Route
					path="/audit/access-checks"
					element={<AccessChecksPage />}
				/>
			</Routes>,
		);
		expect(screen.getByText("Database unavailable")).toBeVisible();
		await user.click(screen.getByRole("button", { name: "Try Again" }));
		expect(refetch).toHaveBeenCalled();
	});

	it("opens a summary directly in the inspector with compact recorded checks", async () => {
		const { user } = renderPage();

		await user.click(screen.getByRole("button", { name: "Nightly Sync" }));

		expect(search()).toBe("?group_by=workflow&key=wf-1");
		expect(
			screen.getByRole("heading", { name: "Nightly Sync" }),
		).toBeVisible();
		expect(audit.list).toHaveBeenLastCalledWith(
			expect.objectContaining({
				action: "access.check",
				outcome: "failure",
				workflow_id: "wf-1",
				limit: 50,
				aggregate: "1h",
			}),
			true,
			{ preservePageData: true },
		);
		const opener = screen.getByRole("button", {
			name: "Open 12 Recorded Checks",
		});
		await user.click(opener);
		const checks = within(
			screen.getByRole("region", { name: "Recorded Checks" }),
		);
		expect(audit.list).toHaveBeenLastCalledWith(
			expect.objectContaining({
				aggregate_key: "window-1",
				workflow_id: "wf-1",
			}),
			true,
			{ preservePageData: true },
		);
		expect(checks.queryByRole("table")).not.toBeInTheDocument();
		expect(checks.getByRole("list", { name: "Check Times" })).toBeVisible();
		expect(
			await screen.findByRole("dialog", { name: "Access Check" }),
		).toBeVisible();
		await user.keyboard("{Escape}");
		await waitFor(() => expect(opener).toHaveFocus());
	});

	it("filters a group with no value by none, and goes back to the groups", async () => {
		const { user } = renderPage(
			"/audit/access-checks?group_by=organization&key=none",
		);

		expect(audit.list).toHaveBeenLastCalledWith(
			expect.objectContaining({ organization_id: "none" }),
			true,
			{ preservePageData: true },
		);
		await user.click(screen.getByRole("link", { name: "Back" }));

		await waitFor(() => expect(search()).toBe("?group_by=organization"));
	});

	it("selects another recorded check inside the same inspector", async () => {
		const grouped = audit.list.getMockImplementation()!;
		const other = {
			...sample,
			id: "event-other",
			timestamp: "2026-10-17T12:30:00Z",
			operation_id: "tables.query",
		};
		audit.list.mockImplementation((params: { aggregate?: string }) =>
			params.aggregate
				? grouped(params)
				: query({ entries: [sample, other], continuation_token: null }),
		);
		const { user } = renderPage(
			"/audit/access-checks?group_by=workflow&key=wf-1",
		);
		await user.click(
			screen.getByRole("button", { name: "Open 12 Recorded Checks" }),
		);
		const dialog = within(
			screen.getByRole("dialog", { name: "Access Check" }),
		);
		const times = within(dialog.getByRole("list", { name: "Check Times" }));
		await user.click(times.getAllByRole("button")[1]);
		expect(
			dialog.getByText("Operation", { selector: "dt" })
				.nextElementSibling,
		).toHaveTextContent("tables.query");
		expect(times.getAllByRole("button")[1]).toHaveAttribute(
			"aria-pressed",
			"true",
		);
		expect(screen.getAllByRole("dialog")).toHaveLength(1);
	});

	it("names workflows and identities' organizations for someone who can't list either", async () => {
		lists.readable = false;
		const { user } = renderPage();

		await user.click(screen.getByRole("button", { name: "Nightly Sync" }));
		await user.click(
			screen.getByRole("button", { name: "Open 12 Recorded Checks" }),
		);
		const dialog = await screen.findByRole("dialog", {
			name: "Access Check",
		});
		const workflow = within(dialog).getByText("Workflow", {
			selector: "dt",
		}).nextElementSibling;
		expect(workflow).toHaveTextContent("Nightly Sync");
	});

	it("opens the same compact inspector on phones with organization first", async () => {
		media.desktop = false;
		const { user } = renderPage(
			"/audit/access-checks?group_by=workflow&key=wf-1",
		);
		await user.click(
			screen.getByRole("button", { name: "Open 12 Recorded Checks" }),
		);
		const dialog = within(
			await screen.findByRole("dialog", { name: "Access Check" }),
		);
		const labels = dialog.getAllByRole("term");
		expect(labels[0]).toHaveTextContent("Organization");
		expect(labels[0].nextElementSibling).toHaveTextContent("Fabrikam");
		expect(dialog.getByRole("list", { name: "Check Times" })).toBeVisible();
		expect(dialog.queryByRole("table")).not.toBeInTheDocument();
		expect(dialog.getByText("Default Identity · Contoso")).toBeVisible();
	});

	it("changes the aggregation window and can show ungrouped checks", async () => {
		const { user } = renderPage(
			"/audit/access-checks?group_by=workflow&key=wf-1",
		);
		expect(
			screen.getByText("1 summary on this page · Page 1"),
		).toBeVisible();
		await user.click(
			screen.getByRole("combobox", { name: "Time Aggregate" }),
		);
		await user.click(screen.getByRole("option", { name: "6 Hours" }));
		expect(audit.list).toHaveBeenLastCalledWith(
			expect.objectContaining({
				aggregate: "6h",
				workflow_id: "wf-1",
				continuation_token: undefined,
			}),
			true,
			{ preservePageData: true },
		);
		await user.click(
			screen.getByRole("combobox", { name: "Time Aggregate" }),
		);
		await user.click(screen.getByRole("option", { name: "Off" }));
		const params = audit.list.mock.lastCall?.[0];
		expect(params).not.toHaveProperty("aggregate");
		expect(params).not.toHaveProperty("aggregate_key");
		expect(screen.getByText("1 event on this page · Page 1")).toBeVisible();
		expect(
			screen.queryByRole("columnheader", { name: "# Checks" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("columnheader", { name: "Decision Point" }),
		).toBeVisible();
	});
});
