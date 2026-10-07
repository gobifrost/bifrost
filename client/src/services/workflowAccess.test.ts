import { beforeEach, describe, expect, it, vi } from "vitest";

const { useQueryMock, useMutationMock, invalidateQueries } = vi.hoisted(() => ({
	useQueryMock: vi.fn(),
	useMutationMock: vi.fn(),
	invalidateQueries: vi.fn(),
}));
const assignmentsQuery = vi.hoisted(() => ({
	data: undefined as unknown,
	refetch: vi.fn(),
}));
const replace = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (...args: unknown[]) => useQueryMock(...args),
		useMutation: (...args: unknown[]) => useMutationMock(...args),
	},
}));

vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({ invalidateQueries }),
}));

vi.mock("@/hooks/useUsers", () => ({
	useUserRoleAssignments: () => assignmentsQuery,
	useReplaceUserRoleAssignments: () => replace,
}));

import { IDENTITIES_QUERY_KEY } from "./identities";
import {
	isDefaultIdentityFor,
	mergeGrant,
	RECOMMENDED_ACCESS_QUERY_KEY,
	rolesGrantableAt,
	RUN_IDENTITIES_QUERY_KEY,
	useGrantToIdentity,
	useSetWorkflowRunIdentity,
	useWorkflowRecommendedAccess,
	useWorkflowRunIdentities,
	type AssignableRole,
	type RoleAssignments,
} from "./workflowAccess";

const CONTOSO = "11111111-1111-4111-8111-111111111111";
const FABRIKAM = "22222222-2222-4222-8222-222222222222";

function assignments(
	additional: RoleAssignments["additional"] = [],
): RoleAssignments {
	return {
		base_role: { id: "role-user", name: "User", is_builtin: true },
		additional,
		is_protected: false,
		assignable_roles: [],
	} as RoleAssignments;
}

describe("workflow access service", () => {
	beforeEach(() => {
		useQueryMock.mockReset();
		useMutationMock.mockReset();
		invalidateQueries.mockReset();
		assignmentsQuery.refetch.mockReset();
		replace.mutateAsync.mockReset();
	});

	it("lists the identities a workflow may run as", () => {
		useWorkflowRunIdentities("wf-1");

		expect(useQueryMock).toHaveBeenCalledWith(
			"get",
			"/api/workflows/{workflow_id}/run-identities",
			{ params: { path: { workflow_id: "wf-1" } } },
			{ enabled: true },
		);
	});

	it("reads the recommended access for the identity the workflow runs as", () => {
		useWorkflowRecommendedAccess(undefined);

		expect(useQueryMock).toHaveBeenCalledWith(
			"get",
			"/api/workflows/{workflow_id}/recommended-access",
			{ params: { path: { workflow_id: undefined } } },
			{ enabled: false },
		);
	});

	it("refreshes workflows, recommendations and identity use after changing who it runs as", () => {
		useSetWorkflowRunIdentity();
		const [method, path, options] = useMutationMock.mock.calls[0] as [
			string,
			string,
			{ onSuccess: () => void },
		];
		options.onSuccess();

		expect([method, path]).toEqual([
			"patch",
			"/api/workflows/{workflow_id}",
		]);
		expect(
			invalidateQueries.mock.calls.map(([filter]) => filter.queryKey),
		).toEqual([
			["get", "/api/workflows"],
			RECOMMENDED_ACCESS_QUERY_KEY,
			RUN_IDENTITIES_QUERY_KEY,
			IDENTITIES_QUERY_KEY,
		]);
	});

	it("treats its organization's default identity as the workflow's default", () => {
		expect(
			isDefaultIdentityFor(
				{ identity_kind: "org_default", organization_id: CONTOSO },
				CONTOSO,
			),
		).toBe(true);
		expect(
			isDefaultIdentityFor(
				{ identity_kind: "global_default", organization_id: null },
				null,
			),
		).toBe(true);
		expect(
			isDefaultIdentityFor(
				{ identity_kind: "custom", organization_id: CONTOSO },
				CONTOSO,
			),
		).toBe(false);
		// A provider-organization workflow may also run as the global identity,
		// which is not its default.
		expect(
			isDefaultIdentityFor(
				{ identity_kind: "global_default", organization_id: null },
				CONTOSO,
			),
		).toBe(false);
	});

	it("adds a granted role to the identity's existing roles, keeping the base role", () => {
		const current = assignments([
			{
				role_id: "role-reader",
				name: "Reader",
				is_builtin: false,
				permissions: [],
				boundaries: [
					{
						kind: "organization",
						organization_id: CONTOSO,
						organization_name: "Contoso",
					},
				],
			},
		]);

		expect(
			mergeGrant(current, {
				role_id: "role-tickets",
				boundaries: [
					{ kind: "organization", organization_id: FABRIKAM },
				],
			}),
		).toEqual({
			base_role_id: "role-user",
			additional: [
				{
					role_id: "role-reader",
					boundaries: [
						{ kind: "organization", organization_id: CONTOSO },
					],
				},
				{
					role_id: "role-tickets",
					boundaries: [
						{ kind: "organization", organization_id: FABRIKAM },
					],
				},
			],
		});
	});

	it("widens a role the identity already holds instead of repeating it", () => {
		const current = assignments([
			{
				role_id: "role-tickets",
				name: "Tickets",
				is_builtin: false,
				permissions: [],
				boundaries: [
					{
						kind: "organization",
						organization_id: CONTOSO,
						organization_name: "Contoso",
					},
				],
			},
		]);

		expect(
			mergeGrant(current, {
				role_id: "role-tickets",
				boundaries: [
					{ kind: "organization", organization_id: CONTOSO },
					{ kind: "platform", organization_id: null },
				],
			}).additional,
		).toEqual([
			{
				role_id: "role-tickets",
				boundaries: [
					{ kind: "organization", organization_id: CONTOSO },
					{ kind: "platform", organization_id: null },
				],
			},
		]);
	});

	it("grants from the identity's current roles, then refreshes what the grant changes", async () => {
		const current = assignments([
			{
				role_id: "role-reader",
				name: "Reader",
				is_builtin: false,
				permissions: [],
				boundaries: [
					{
						kind: "organization",
						organization_id: CONTOSO,
						organization_name: "Contoso",
					},
				],
			},
		]);
		assignmentsQuery.refetch.mockResolvedValue({ data: current });
		replace.mutateAsync.mockResolvedValue(current);
		const grant = {
			role_id: "role-tickets",
			boundaries: [
				{ kind: "organization" as const, organization_id: FABRIKAM },
			],
		};

		await useGrantToIdentity("identity-1").grant(grant);

		expect(assignmentsQuery.refetch).toHaveBeenCalled();
		expect(replace.mutateAsync).toHaveBeenCalledWith({
			params: { path: { user_id: "identity-1" } },
			body: mergeGrant(current, grant),
		});
		expect(
			invalidateQueries.mock.calls.map(([filter]) => filter.queryKey),
		).toEqual([
			RECOMMENDED_ACCESS_QUERY_KEY,
			RUN_IDENTITIES_QUERY_KEY,
			IDENTITIES_QUERY_KEY,
		]);
	});

	it("offers the roles that can be granted at an organization", () => {
		const PROVIDER = "33333333-3333-4333-8333-333333333333";
		const role = (overrides: Partial<AssignableRole>): AssignableRole => ({
			id: "role-tickets",
			name: "Tickets",
			is_builtin: false,
			permissions: [],
			can_be_base: false,
			can_be_additional: true,
			boundary_kinds: ["organization"],
			provider_organization_allowed: false,
			fixed_boundaries: [],
			...overrides,
		});
		const roles = [
			role({}),
			role({ id: "role-base-only", can_be_additional: false }),
			role({
				id: "role-managed",
				boundary_kinds: ["managed_organizations"],
			}),
			role({
				id: "role-fixed",
				fixed_boundaries: [{ kind: "platform", organization_id: null }],
			}),
			role({ id: "role-provider", provider_organization_allowed: true }),
		];

		expect(
			rolesGrantableAt(roles, FABRIKAM, PROVIDER).map((r) => r.id),
		).toEqual(["role-tickets", "role-provider"]);
		// The provider organization takes only roles allowed there.
		expect(
			rolesGrantableAt(roles, PROVIDER, PROVIDER).map((r) => r.id),
		).toEqual(["role-provider"]);
	});

	it("grants nothing when the current roles can't be read", async () => {
		const failure = new Error("offline");
		assignmentsQuery.refetch.mockResolvedValue({
			data: undefined,
			error: failure,
		});

		await expect(
			useGrantToIdentity("identity-1").grant({
				role_id: "role-tickets",
				boundaries: [],
			}),
		).rejects.toBe(failure);
		expect(replace.mutateAsync).not.toHaveBeenCalled();
	});
});
