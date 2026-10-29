import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";

const state = vi.hoisted(() => ({
	result: {} as Record<string, unknown>,
	calls: [] as unknown[],
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoleUsersPage: (...args: unknown[]) => {
		state.calls.push(args);
		return state.result;
	},
}));

import { RolePeoplePanel } from "./RolePeoplePanel";

const olivia = {
	id: "u-1",
	name: "Olivia Operator",
	email: "olivia@provider.test",
	organization_id: "provider",
	organization_name: "Provider",
	organization_is_provider: true,
	boundaries: [
		{ kind: "managed_organizations", organization_id: null },
		{
			kind: "organization",
			organization_id: "org-a",
			organization_name: "Contoso",
		},
	],
};

beforeEach(() => {
	state.calls = [];
	state.result = {
		data: { user_ids: ["u-1"], users: [olivia], total: 1 },
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	};
});

describe("RolePeoplePanel", () => {
	it("lists each holder with their organization and where the role applies", () => {
		renderWithProviders(<RolePeoplePanel roleId="operator" />);

		expect(
			screen.getByRole("link", { name: "Olivia Operator" }),
		).toHaveAttribute("href", "/users/u-1");
		expect(
			screen.getByText("olivia@provider.test · Provider"),
		).toBeInTheDocument();
		const places = screen.getByRole("list", {
			name: "Where it applies for Olivia Operator",
		});
		expect(
			within(places).getByText("All Customer Organizations"),
		).toBeInTheDocument();
		expect(within(places).getByText("Contoso")).toBeInTheDocument();
		expect(state.calls[0]).toEqual([
			"operator",
			{ search: "", limit: 25, offset: 0 },
		]);
	});

	it("shows an identity by glyph, name and organization, without its email", () => {
		const identity = {
			id: "i-1",
			name: "Default Identity",
			email: "identity-i-1@identities.bifrost.internal",
			organization_id: "org-a",
			organization_name: "Contoso",
			organization_is_provider: false,
			identity_kind: "org_default",
			boundaries: [],
		};
		state.result = {
			...state.result,
			data: { user_ids: ["i-1"], users: [identity], total: 1 },
		};
		renderWithProviders(<RolePeoplePanel roleId="operator" />);

		const link = screen.getByRole("link", { name: /Default Identity/ });
		expect(link).toHaveAttribute("href", "/users/i-1");
		expect(
			within(link).getByRole("img", { name: "Identity" }),
		).toBeInTheDocument();
		expect(within(link).getByLabelText("Organization")).toHaveTextContent(
			"Contoso",
		);
		expect(screen.queryByText(/identities\.bifrost\.internal/)).toBeNull();
	});

	it("says when no one holds the role", () => {
		state.result = {
			...state.result,
			data: { user_ids: [], users: [], total: 0 },
		};
		renderWithProviders(<RolePeoplePanel roleId="operator" />);

		expect(
			screen.getByText("No one has this role yet."),
		).toBeInTheDocument();
	});

	it("offers a retry when the list cannot be loaded", async () => {
		const refetch = vi.fn();
		state.result = {
			data: undefined,
			isLoading: false,
			isError: true,
			error: {
				detail: "You don't have permission to view role assignments",
			},
			isFetching: false,
			refetch,
		};
		const { user } = renderWithProviders(
			<RolePeoplePanel roleId="operator" />,
		);

		expect(
			screen.getByText(
				"You don't have permission to view role assignments",
			),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Retry People" }));
		expect(refetch).toHaveBeenCalled();
	});
});
