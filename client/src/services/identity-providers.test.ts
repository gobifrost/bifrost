import { beforeEach, describe, expect, it, vi } from "vitest";

const { useQueryMock, useMutationMock, invalidateQueries } = vi.hoisted(() => ({
	useQueryMock: vi.fn(),
	useMutationMock: vi.fn(),
	invalidateQueries: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (...args: unknown[]) => useQueryMock(...args),
		useMutation: (...args: unknown[]) => useMutationMock(...args),
	},
}));

vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({ invalidateQueries }),
}));

import {
	IDENTITY_PROVIDERS_QUERY_KEY,
	credentialStatus,
	identitySourceLabel,
	mappedGroupsLabel,
	useDeleteIdentityProvider,
	useIdentityProvider,
	useIdentityProviders,
	useMapIdentityProviderGroup,
	useOrganizationDomains,
	useRotateIdentityProviderToken,
	useVerifyOrganizationDomain,
	vendorLabel,
} from "./identity-providers";

const DAY = 24 * 60 * 60 * 1000;

/** The keys a mutation hook's onSuccess invalidates. */
function invalidatedBy(hook: () => unknown): unknown[] {
	hook();
	const options = useMutationMock.mock.calls[0][2] as {
		onSuccess: () => void;
	};
	options.onSuccess();
	return invalidateQueries.mock.calls.map(([filter]) => filter.queryKey);
}

describe("identity providers service", () => {
	beforeEach(() => {
		useQueryMock.mockReset();
		useMutationMock.mockReset();
		invalidateQueries.mockReset();
	});

	it("reads connections and one connection from their endpoints", () => {
		useIdentityProviders();
		useIdentityProvider("idp-1");

		expect(useQueryMock).toHaveBeenNthCalledWith(
			1,
			"get",
			"/api/identity-providers",
		);
		expect(useQueryMock).toHaveBeenNthCalledWith(
			2,
			"get",
			"/api/identity-providers/{identity_provider_id}",
			{ params: { path: { identity_provider_id: "idp-1" } } },
		);
	});

	it("scopes domain claims to one organization", () => {
		useOrganizationDomains("org-1", { enabled: false });

		expect(useQueryMock).toHaveBeenCalledWith(
			"get",
			"/api/identity-providers/domains",
			{ params: { query: { organization_id: "org-1" } } },
			{ enabled: false },
		);
	});

	it("refreshes the list and detail after a token rotation", () => {
		expect(invalidatedBy(useRotateIdentityProviderToken)).toEqual([
			[...IDENTITY_PROVIDERS_QUERY_KEY],
			["get", "/api/identity-providers/{identity_provider_id}"],
		]);
	});

	it("refreshes users after a mapping change, since it changes their roles", () => {
		expect(invalidatedBy(useMapIdentityProviderGroup)).toContainEqual([
			"get",
			"/api/users",
		]);
	});

	it("refreshes users after deleting a connection, since they become Native", () => {
		expect(invalidatedBy(useDeleteIdentityProvider)).toContainEqual([
			"get",
			"/api/users",
		]);
	});

	it("refreshes connections after verifying a domain, which they list", () => {
		expect(invalidatedBy(useVerifyOrganizationDomain)).toContainEqual([
			...IDENTITY_PROVIDERS_QUERY_KEY,
		]);
	});

	it("names each vendor", () => {
		expect(vendorLabel("entra")).toBe("Microsoft Entra ID");
		expect(vendorLabel("other")).toBe("Other SCIM 2.0");
	});

	it("flags tokens that are missing, expiring within 14 days, or expired", () => {
		const now = new Date("2026-11-16T12:00:00Z");
		const at = (offset: number) => ({
			expires_at: new Date(now.getTime() + offset).toISOString(),
		});

		expect(credentialStatus(null, now)).toBe("none");
		expect(credentialStatus(at(30 * DAY), now)).toBe("active");
		expect(credentialStatus(at(14 * DAY), now)).toBe("expiring");
		expect(credentialStatus(at(-1), now)).toBe("expired");
	});

	it("names a user's identity source, or Native when none manages them", () => {
		expect(identitySourceLabel({ identity_source: null })).toBe("Native");
		expect(
			identitySourceLabel({
				identity_source: {
					identity_provider_id: "idp-1",
					name: "Contoso Entra ID",
					vendor: "entra",
					activating: false,
				},
			}),
		).toBe("Contoso Entra ID");
	});

	it("shows mapped groups out of all groups", () => {
		expect(
			mappedGroupsLabel({ mapped_group_count: 2, group_count: 3 }),
		).toBe("2/3");
	});
});
