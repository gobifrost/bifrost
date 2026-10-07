import { beforeEach, describe, expect, it, vi } from "vitest";
import { within } from "@testing-library/react";

import { renderWithProviders, screen } from "@/test-utils";
import type { TraceNames } from "@/lib/access-trace";
import type { AuditLogEntry } from "@/hooks/useAuditLog";
import type { AccessExplanation, AccessTrace } from "@/services/access";

const explain = vi.hoisted(() => ({
	calls: [] as Array<[string, boolean]>,
	data: undefined as AccessExplanation | undefined,
	error: null as unknown,
	refetch: vi.fn(),
}));

vi.mock("@/hooks/useAuditLog", () => ({
	useAuditExplain: (eventId: string, enabled: boolean) => {
		explain.calls.push([eventId, enabled]);
		return {
			data: enabled ? explain.data : undefined,
			error: enabled ? explain.error : null,
			isFetching: false,
			refetch: explain.refetch,
		};
	},
}));

const names: TraceNames = {
	role: () => undefined,
	organization: (id) => ({ "org-1": "Contoso", "org-2": "Fabrikam" })[id],
	permission: (permission) => permission,
};
vi.mock("@/hooks/useTraceNames", () => ({
	useTraceNames: () => ({ names, organizationNames: new Map() }),
}));

vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => ({
		data: {
			workflows: [
				{
					id: "wf-1",
					name: "nightly_sync",
					display_name: "Nightly Sync",
				},
			],
		},
	}),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ canAnywhere: () => true }),
}));

import { AuditEventDrawer } from "./AuditEventDrawer";

const then: AccessTrace = {
	outcome: "success",
	enforced: false,
	steps: [
		{
			key: "run_user",
			label: "Run user",
			status: "passed",
			reason: "identity",
			facts: {
				user_id: "identity-1",
				identity_kind: "default",
				home_organization_id: "org-1",
				is_platform_admin: false,
			},
		},
		{
			key: "target",
			label: "Target in reach",
			status: "passed",
			reason: "home",
			facts: { organization_id: "org-1" },
		},
	],
};
const now: AccessTrace = {
	...then,
	outcome: "failure",
	steps: [
		then.steps[0],
		{
			key: "target",
			label: "Target in reach",
			status: "stopped",
			reason: "outside",
			facts: { organization_id: "org-2" },
		},
	],
};

const accessCheck: AuditLogEntry = {
	id: "event-1",
	timestamp: "2026-10-05T12:00:00Z",
	action: "access.check",
	resource_type: "entry",
	resource_id: null,
	outcome: "success",
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
	},
	ip_address: null,
	user_agent: null,
	details: { workflow_id: "wf-1", trace: then },
};

const roleUpdate: AuditLogEntry = {
	...accessCheck,
	id: "event-2",
	action: "role.update",
	resource_type: "role",
	resource_id: "33333333-3333-3333-3333-333333333333",
	actor: {
		user_id: "user-1",
		user_email: "avery@contoso.example",
		user_name: "Avery Example",
		organization_id: "org-1",
		organization_name: "Contoso",
	},
	details: { role_name: "Helpdesk", permissions_added: ["tables.read"] },
};

function renderDrawer(entry: AuditLogEntry) {
	return renderWithProviders(
		<AuditEventDrawer entry={entry} open onOpenChange={vi.fn()} />,
	);
}

function field(label: string) {
	const term = within(screen.getByRole("dialog")).getByText(label, {
		selector: "dt",
	});
	return term.nextElementSibling as HTMLElement;
}

beforeEach(() => {
	explain.calls = [];
	explain.data = undefined;
	explain.error = null;
	explain.refetch.mockReset();
});

describe("AuditEventDrawer", () => {
	it("summarizes an access check and shows its stored trace", () => {
		renderDrawer(accessCheck);

		const dialog = screen.getByRole("dialog", { name: "Access Check" });
		expect(field("Action")).toHaveTextContent("access.check");
		expect(field("Outcome")).toHaveTextContent("success");
		expect(field("Organization")).toHaveTextContent("Fabrikam");
		expect(field("Resource")).toHaveTextContent("Workflow or Agent Access");
		expect(field("Workflow")).toHaveTextContent("Nightly Sync");
		expect(
			within(dialog).getByRole("list", { name: "Then" }),
		).toHaveTextContent("Contoso is their home organization.");
		expect(explain.calls.every(([, enabled]) => !enabled)).toBe(true);
	});

	it("shows an identity run user with its glyph and organization", () => {
		renderDrawer(accessCheck);

		const actor = field("Actor");
		expect(
			within(actor).getByRole("img", { name: "Identity" }),
		).toBeVisible();
		expect(actor).toHaveTextContent("Default Identity · Contoso");
	});

	it("tests the check again and shows then and now with the changes marked", async () => {
		explain.data = {
			event: accessCheck,
			then,
			now,
			now_unavailable: null,
			changed: true,
		};
		const { user } = renderDrawer(accessCheck);

		await user.click(
			screen.getByRole("button", { name: "Test Again Now" }),
		);

		expect(explain.calls.at(-1)).toEqual(["event-1", true]);
		const nowList = screen.getByRole("list", { name: "Now" });
		const [runUser, target] = within(nowList).getAllByRole("listitem");
		expect(target).toHaveTextContent("Changed");
		expect(runUser).not.toHaveTextContent("Changed");
		expect(screen.getByRole("list", { name: "Then" })).toBeVisible();
		expect(
			screen.getByText("Now this check would stop at Target in Reach."),
		).toBeVisible();
	});

	it("tests again by refetching once the answer is shown", async () => {
		explain.data = {
			event: accessCheck,
			then,
			now: then,
			now_unavailable: null,
			changed: false,
		};
		const { user } = renderDrawer(accessCheck);
		const button = screen.getByRole("button", { name: "Test Again Now" });

		await user.click(button);
		expect(screen.getByText("Nothing changed since then.")).toBeVisible();
		await user.click(button);

		expect(explain.refetch).toHaveBeenCalledTimes(1);
	});

	it("explains why now is unavailable instead of a Now column", async () => {
		explain.data = {
			event: accessCheck,
			then,
			now: null,
			now_unavailable: "workflow_missing",
			changed: null,
		};
		const { user } = renderDrawer(accessCheck);

		await user.click(
			screen.getByRole("button", { name: "Test Again Now" }),
		);

		expect(
			screen.getByText(
				"The workflow no longer exists, so this check can't be tested again.",
			),
		).toBeVisible();
		expect(
			screen.queryByRole("list", { name: "Now" }),
		).not.toBeInTheDocument();
	});

	it("says an archived event must be exported", async () => {
		explain.error = {
			detail: "Audit event not found. Events older than 90 days are archived; export that day from the audit log to see them.",
		};
		const { user } = renderDrawer(accessCheck);

		await user.click(
			screen.getByRole("button", { name: "Test Again Now" }),
		);

		expect(
			screen.getByText(
				"This event is archived. Export it to see its details.",
			),
		).toBeVisible();
	});

	it("shows any other event's summary and details, without a trace", () => {
		renderDrawer(roleUpdate);

		screen.getByRole("dialog", { name: "Audit Event" });
		expect(field("Action")).toHaveTextContent("role.update");
		expect(field("Actor")).toHaveTextContent("avery@contoso.example");
		expect(field("Resource")).toHaveTextContent(
			"role / 33333333-3333-3333-3333-333333333333",
		);
		expect(field("Role Name")).toHaveTextContent("Helpdesk");
		expect(field("Permissions Added")).toHaveTextContent('["tables.read"]');
		expect(
			screen.queryByRole("button", { name: "Test Again Now" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("list", { name: "Then" }),
		).not.toBeInTheDocument();
	});
});
