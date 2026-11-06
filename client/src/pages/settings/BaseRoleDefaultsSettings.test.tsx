import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

const useQuery = vi.fn();
const useMutation = vi.fn();
const baseRoleSelect = vi.fn();
const mutateAsync = vi.fn();

vi.mock("@/lib/api-client", () => ({
	$api: {
		queryOptions: () => ({ queryKey: ["base-role-defaults"] }),
		useQuery: (...args: unknown[]) => useQuery(...args),
		useMutation: (...args: unknown[]) => useMutation(...args),
	},
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		meets: () => true,
		canAt: () => true,
	}),
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		onChange,
	}: {
		onChange: (value: string | null) => void;
	}) => (
		<button type="button" onClick={() => onChange("org-1")}>
			Organization scope
		</button>
	),
}));
vi.mock("@/components/users/BaseRoleSelect", () => ({
	BaseRoleSelect: ({
		value,
		onChange,
		defaultLabel,
	}: {
		value: string | null;
		onChange: (value: string | null) => void;
		defaultLabel: string;
	}) => {
		baseRoleSelect({ value });
		return (
			<>
				<button type="button" onClick={() => onChange("support-role")}>
					Choose Support
				</button>
				<button type="button" onClick={() => onChange(null)}>
					{defaultLabel}
				</button>
			</>
		);
	},
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

import { BaseRoleDefaultsSettings } from "./BaseRoleDefaultsSettings";

describe("BaseRoleDefaultsSettings", () => {
	beforeEach(() => {
		baseRoleSelect.mockReset();
		useQuery.mockReturnValue({
			data: {
				default_base_role_id: null,
				effective_base_role_id: "user-role",
				effective_base_role_name: "User",
				source: "global",
			},
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
		mutateAsync.mockReset().mockResolvedValue({
			default_base_role_id: "support-role",
			effective_base_role_id: "support-role",
			effective_base_role_name: "Support",
			source: "global",
		});
		useMutation.mockReturnValue({
			isPending: false,
			mutateAsync,
		});
	});

	it("shows the effective role and saves an explicit Global default", async () => {
		const { user } = renderWithProviders(<BaseRoleDefaultsSettings />);

		expect(
			screen.getByText(/new accounts currently receive/i),
		).toHaveTextContent("User");
		expect(
			screen.getByRole("button", { name: "Save Default" }),
		).toBeDisabled();
		await user.click(
			screen.getByRole("button", { name: "Choose Support" }),
		);
		await user.click(screen.getByRole("button", { name: "Save Default" }));

		await waitFor(() =>
			expect(mutateAsync).toHaveBeenCalledWith({
				params: { query: {} },
				body: { default_base_role_id: "support-role" },
			}),
		);
	});

	it("saves null to reset a populated organization override instead of retaining its old role", async () => {
		useQuery.mockImplementation(
			(
				_method: unknown,
				_path: unknown,
				init: { params: { query: { organization_id?: string } } },
			) => ({
				data: init.params.query.organization_id
					? {
							default_base_role_id: "old-role",
							effective_base_role_id: "old-role",
							effective_base_role_name: "Old Role",
							source: "organization",
						}
					: {
							default_base_role_id: null,
							effective_base_role_id: "user-role",
							effective_base_role_name: "User",
							source: "global",
						},
				isLoading: false,
				isError: false,
				isFetching: false,
				refetch: vi.fn(),
			}),
		);
		const { user } = renderWithProviders(<BaseRoleDefaultsSettings />);

		await user.click(
			screen.getByRole("button", { name: "Organization scope" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Inherit Global Default" }),
		);
		await user.click(screen.getByRole("button", { name: "Save Default" }));

		await waitFor(() =>
			expect(mutateAsync).toHaveBeenCalledWith({
				params: { query: { organization_id: "org-1" } },
				body: { default_base_role_id: null },
			}),
		);
	});
});
