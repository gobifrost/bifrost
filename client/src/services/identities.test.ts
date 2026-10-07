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

import { USER_ACCESS_QUERY_KEY } from "./access";
import {
	IDENTITIES_QUERY_KEY,
	identityKindLabel,
	identityLabel,
	identityOrganization,
	useCreateIdentity,
	useDeleteIdentity,
	useIdentities,
	useRenameIdentity,
} from "./identities";

/** The keys a mutation hook's onSuccess invalidates. */
function invalidatedBy(hook: () => unknown): unknown[] {
	hook();
	const options = useMutationMock.mock.calls[0][2] as {
		onSuccess: () => void;
	};
	options.onSuccess();
	return invalidateQueries.mock.calls.map(([filter]) => filter.queryKey);
}

describe("identities service", () => {
	beforeEach(() => {
		useQueryMock.mockReset();
		useMutationMock.mockReset();
		invalidateQueries.mockReset();
	});

	it("lists identities from their endpoint", () => {
		useIdentities();

		expect(useQueryMock).toHaveBeenCalledWith("get", "/api/identities");
	});

	it("names each kind the way the Identities list shows it", () => {
		expect(identityKindLabel("org_default")).toBe("Default");
		expect(identityKindLabel("global_default")).toBe("Global");
		expect(identityKindLabel("custom")).toBe("Custom");
	});

	it("places an identity in its organization, or Global", () => {
		expect(identityOrganization({ organization_name: "Contoso" })).toBe(
			"Contoso",
		);
		expect(identityOrganization({ organization_name: null })).toBe(
			"Global",
		);
	});

	it("names an identity with its organization, since every default is named alike", () => {
		expect(
			identityLabel({
				name: "Default Identity",
				organization_name: "Contoso",
			}),
		).toBe("Default Identity · Contoso");
		expect(
			identityLabel({
				name: "Default Identity",
				organization_name: null,
			}),
		).toBe("Default Identity · Global");
		expect(
			identityLabel({
				name: "Nightly Sync Identity",
				organization_name: "Fabrikam",
			}),
		).toBe("Nightly Sync Identity · Fabrikam");
	});

	it("refreshes the list after creating an identity", () => {
		expect(invalidatedBy(useCreateIdentity)).toEqual([
			IDENTITIES_QUERY_KEY,
		]);
		expect(useMutationMock.mock.calls[0].slice(0, 2)).toEqual([
			"post",
			"/api/identities",
		]);
	});

	it("refreshes the list, the person page and access after a rename", () => {
		expect(invalidatedBy(useRenameIdentity)).toEqual([
			IDENTITIES_QUERY_KEY,
			["get", "/api/users/{user_id}"],
			USER_ACCESS_QUERY_KEY,
		]);
		expect(useMutationMock.mock.calls[0].slice(0, 2)).toEqual([
			"patch",
			"/api/identities/{identity_id}",
		]);
	});

	it("refreshes the list after deleting an identity", () => {
		expect(invalidatedBy(useDeleteIdentity)).toEqual([
			IDENTITIES_QUERY_KEY,
		]);
		expect(useMutationMock.mock.calls[0].slice(0, 2)).toEqual([
			"delete",
			"/api/identities/{identity_id}",
		]);
	});
});
