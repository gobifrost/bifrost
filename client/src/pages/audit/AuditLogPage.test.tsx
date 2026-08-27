import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUseAuditLog = vi.fn();

vi.mock("@/hooks/useAuditLog", () => ({
	useAuditLog: (params: unknown) => mockUseAuditLog(params),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: true }),
}));

import { AuditLogPage } from "./AuditLogPage";

const filePath = "denied/quarterly-result.csv";

beforeEach(() => {
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
				"restricted_customers / 44444444-4444-4444-4444-444444444444",
			),
		).toBeInTheDocument();

		await user.click(screen.getByRole("combobox", { name: "Action filter" }));
		await user.click(
			await screen.findByRole("option", { name: "Policy denials" }),
		);
		await user.click(screen.getByRole("combobox", { name: "Outcome filter" }));
		await user.click(await screen.findByRole("option", { name: "Failure" }));
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
			);
		});
		expect(
			screen.getByRole("button", { name: "Clear filters" }),
		).toBeVisible();
	});
});
