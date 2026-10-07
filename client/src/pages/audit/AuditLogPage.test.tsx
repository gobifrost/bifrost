import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUseAuditLog = vi.fn();
const mockUseAuth = vi.fn();

vi.mock("@/hooks/useAuditLog", () => ({
	useAuditLog: (...args: unknown[]) => mockUseAuditLog(...args),
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

vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => ({ data: { workflows: [] } }),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ canAnywhere: () => true }),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [],
		isLoading: false,
		isFetching: false,
		error: null,
		refetch: vi.fn(),
	}),
}));

import { AuditLogPage } from "./AuditLogPage";

const filePath = "denied/quarterly-result.csv";

beforeEach(() => {
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseAuditLog.mockReturnValue({
		data: {
			entries: [
				{
					id: "11111111-1111-1111-1111-111111111111",
					timestamp: "2026-08-25T12:00:00Z",
					action: "policy.deny",
					resource_type: "file",
					resource_id: null,
					outcome: "failure",
					source: "http",
					actor: {
						user_id: "22222222-2222-2222-2222-222222222222",
						user_email: "auditor@example.com",
						user_name: "Auditor",
						organization_id: null,
						organization_name: null,
					},
					ip_address: "192.0.2.10",
					user_agent: "vitest",
					details: {
						policy_action: "subscribe",
						location: "reports",
						path: filePath,
						scope: "org-1",
						solution_id: null,
					},
				},
				{
					id: "44444444-4444-4444-4444-444444444444",
					timestamp: "2026-08-25T12:02:00Z",
					action: "organization.update",
					resource_type: "organization",
					resource_id:
						"very-long-resource-identifier-with-multiple-segments-abcdefghijklmnopqrstuvwxyz0123456789",
					outcome: "success",
					source: "http",
					actor: {
						user_id: "66666666-6666-6666-6666-666666666666",
						user_email: "auditor@example.com",
						user_name: "Auditor",
						organization_id: null,
						organization_name: null,
					},
					ip_address: "192.0.2.11",
					user_agent: "vitest",
					details: {
						path: "operations/audit/very-long-context-path-with-many-segments-and-values/abcdefghijklmnopqrstuvwxyz0123456789",
						location: "settings",
						scope: "org-1",
						solution_id: null,
					},
				},
				{
					id: "33333333-3333-3333-3333-333333333333",
					timestamp: "2026-08-25T12:01:00Z",
					action: "policy.deny",
					resource_type: "table_document",
					resource_id: null,
					outcome: "failure",
					source: "websocket",
					actor: {
						user_id: "55555555-5555-5555-5555-555555555555",
						user_email: "subscriber@example.com",
						user_name: "Subscriber",
						organization_id: null,
						organization_name: null,
					},
					ip_address: null,
					user_agent: null,
					details: {
						policy_action: "subscribe",
						table_name: "restricted_customers",
						table_id: "44444444-4444-4444-4444-444444444444",
					},
				},
			],
			continuation_token: null,
		},
		isLoading: false,
		error: null,
		refetch: vi.fn(),
	});
});

describe("AuditLogPage policy filters", () => {
	it("shows denial context and searches it across the API result set", async () => {
		const { user } = renderWithProviders(<AuditLogPage />);

		expect(screen.getByText(`reports / ${filePath}`)).toBeInTheDocument();
		expect(
			screen.getByText(
				"organization / very-long-resource-identifier-with-multiple-segments-abcdefghijklmnopqrstuvwxyz0123456789",
			),
		).toBeInTheDocument();
		expect(
			screen.getByText(
				"settings / operations/audit/very-long-context-path-with-many-segments-and-values/abcdefghijklmnopqrstuvwxyz0123456789",
			),
		).toBeInTheDocument();
		expect(
			screen.getByText(
				"restricted_customers / 44444444-4444-4444-4444-444444444444",
			),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("combobox", { name: "Action filter" }),
		);
		await user.click(
			await screen.findByRole("option", { name: "Policy denials" }),
		);
		await user.click(
			screen.getByRole("combobox", { name: "Outcome filter" }),
		);
		await user.click(
			await screen.findByRole("option", { name: "Failure" }),
		);
		fireEvent.change(
			screen.getByRole("searchbox", { name: "Search audit events" }),
			{ target: { value: filePath } },
		);

		await waitFor(() => {
			expect(mockUseAuditLog).toHaveBeenLastCalledWith(
				expect.objectContaining({
					action: "policy.deny",
					outcome: "failure",
					search: filePath,
				}),
				true,
				{ preservePageData: true },
			);
		});
		expect(
			screen.getByRole("button", { name: "Clear filters" }),
		).toBeVisible();
	});

	it("shows a platform-admin-only access denied state", () => {
		mockUseAuth.mockReturnValueOnce({
			isPlatformAdmin: false,
		});

		renderWithProviders(<AuditLogPage />);

		expect(
			screen.getByText(
				"You do not have permission to view the audit log. Platform administrator access is required.",
			),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Go to Home" }),
		).toBeVisible();
	});

	it("keeps the existing audit table visible and busy while a page fetch is pending", () => {
		mockUseAuditLog.mockReturnValueOnce({
			data: {
				entries: [
					{
						id: "page-one",
						timestamp: "2026-08-25T12:00:00Z",
						action: "policy.deny",
						resource_type: "file",
						resource_id: null,
						outcome: "failure",
						source: "http",
						actor: {
							user_email: "auditor@example.com",
							user_name: "Auditor",
						},
						ip_address: "192.0.2.10",
						details: { location: "reports", path: filePath },
					},
				],
				continuation_token: "next-page",
			},
			isLoading: false,
			isFetching: true,
			error: null,
			refetch: vi.fn(),
		});

		renderWithProviders(<AuditLogPage />);

		expect(
			screen.getByRole("table").closest("[aria-busy]"),
		).toHaveAttribute("aria-busy", "true");
		expect(screen.getByText(`reports / ${filePath}`)).toBeInTheDocument();
		expect(screen.getByLabelText("Loading page")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
	});

	it("shows the retention window and exports with the current action filter", async () => {
		mockUseAuditLog.mockReturnValue({
			data: {
				entries: [],
				continuation_token: null,
				retention: {
					hot_days: 90,
					archive_days: 365,
					oldest_in_database: "2026-07-06T09:30:00Z",
					archived_through: "2026-07-05T23:59:00Z",
					archived_segments: 12,
					archived_rows: 4200,
				},
			},
			isLoading: false,
			error: null,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(<AuditLogPage />);

		const banner = screen.getByRole("region", { name: "Audit retention" });
		expect(banner).toHaveTextContent("Older events are archived through");
		await user.click(
			screen.getByRole("combobox", { name: "Action filter" }),
		);
		await user.click(
			await screen.findByRole("option", { name: "Policy denials" }),
		);
		await user.click(screen.getByRole("button", { name: "Export" }));

		const dialog = await screen.findByRole("dialog", {
			name: "Export audit events",
		});
		expect(dialog).toBeVisible();
		expect(screen.getByLabelText("Action prefix (optional)")).toHaveValue(
			"policy.deny",
		);
	});

	it("shows no retention window when the list omits it", () => {
		renderWithProviders(<AuditLogPage />);

		expect(
			screen.queryByRole("region", { name: "Audit retention" }),
		).not.toBeInTheDocument();
	});

	it("opens an event from the keyboard and returns focus to its row on close", async () => {
		const { user } = renderWithProviders(<AuditLogPage />);

		const open = screen.getByRole("button", {
			name: "organization.update",
		});
		open.focus();
		await user.keyboard("{Enter}");

		const dialog = await screen.findByRole("dialog", {
			name: "Audit Event",
		});
		expect(dialog).toHaveTextContent("organization.update");
		await user.keyboard("{Escape}");

		await waitFor(() =>
			expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
		);
		expect(open).toHaveFocus();
	});

	it("opens an event from a click anywhere on its row", async () => {
		const { user } = renderWithProviders(<AuditLogPage />);

		await user.click(screen.getByText("192.0.2.11"));

		expect(
			await screen.findByRole("dialog", { name: "Audit Event" }),
		).toHaveTextContent("organization.update");
		await user.keyboard("{Escape}");
		await waitFor(() =>
			expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
		);
		expect(
			screen.getByRole("button", { name: "organization.update" }),
		).toHaveFocus();
	});
});
