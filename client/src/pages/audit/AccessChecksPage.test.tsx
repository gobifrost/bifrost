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
	audit.list.mockReturnValue(
		query({ entries: [sample], continuation_token: null }),
	);
});

describe("AccessChecksPage", () => {
	it("lists would-deny checks grouped by workflow", () => {
		renderPage();

		expect(
			screen.getByRole("heading", { name: "Access Checks" }),
		).toBeVisible();
		expect(
			screen.getByText(
				"Checks that would have been denied if enforcement were on. Nothing is blocked yet.",
			),
		).toBeVisible();
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
			screen.getByRole("columnheader", { name: "Would Stop Here" }),
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

		expect(screen.getByText("No Would-Deny Checks")).toBeVisible();
		expect(
			screen.getByText("Every recorded check would have been allowed."),
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

	it("drills into a group's checks and opens one", async () => {
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
			}),
			true,
			{ preservePageData: true },
		);
		expect(
			screen
				.getAllByRole("columnheader")
				.map((header) => header.textContent),
		).toEqual([
			"Organization",
			"Time",
			"Run User",
			"Resource Type",
			"Would Stop Here",
		]);
		const [, row] = screen.getAllByRole("row");
		const cells = within(row).getAllByRole("cell");
		expect(cells[0]).toHaveTextContent("Fabrikam");
		expect(cells[2]).toHaveTextContent("Default Identity · Contoso");

		await user.click(within(row).getByRole("button"));
		expect(
			await screen.findByRole("dialog", { name: "Access Check" }),
		).toBeVisible();
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

	it("names workflows and identities' organizations for someone who can't list either", async () => {
		lists.readable = false;
		const { user } = renderPage();

		await user.click(screen.getByRole("button", { name: "Nightly Sync" }));
		const [, row] = screen.getAllByRole("row");
		expect(row).toHaveTextContent("Default Identity · Contoso");

		await user.click(within(row).getByRole("button"));
		const dialog = await screen.findByRole("dialog", {
			name: "Access Check",
		});
		const workflow = within(dialog).getByText("Workflow", {
			selector: "dt",
		}).nextElementSibling;
		expect(workflow).toHaveTextContent("Nightly Sync");
	});

	it("leads each check's phone record with its organization", async () => {
		media.desktop = false;
		const { user } = renderPage(
			"/audit/access-checks?group_by=workflow&key=wf-1",
		);

		const [record] = within(
			screen.getByRole("list", { name: "Checks" }),
		).getAllByRole("listitem");
		const labels = within(record).getAllByRole("term");
		expect(labels.map((label) => label.textContent)).toEqual([
			"Organization",
			"Time",
			"Run User",
			"Resource Type",
		]);
		expect(labels[0].nextElementSibling).toHaveTextContent("Fabrikam");
		expect(labels[2].nextElementSibling).toHaveTextContent(
			"Default Identity · Contoso",
		);
		expect(record).toHaveTextContent("Would Stop Here · Target in Reach");

		await user.click(within(record).getByRole("button"));
		expect(
			await screen.findByRole("dialog", { name: "Access Check" }),
		).toBeVisible();
	});
});
