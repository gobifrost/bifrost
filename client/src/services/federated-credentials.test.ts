import { beforeEach, describe, expect, it, vi } from "vitest";

const { query, mutation, invalidateQueries } = vi.hoisted(() => ({
	query: vi.fn(),
	mutation: vi.fn(),
	invalidateQueries: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: query, useMutation: mutation },
}));
vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({ invalidateQueries }),
}));
import {
	useCreateFederatedCredential,
	useDeleteFederatedCredential,
	useFederatedCredentials,
	useUpdateFederatedCredential,
} from "./federated-credentials";

describe("Identity federated credential service", () => {
	beforeEach(() => vi.clearAllMocks());
	it("scopes reads to the selected identity", () => {
		useFederatedCredentials("identity-a");
		expect(query).toHaveBeenCalledWith(
			"get",
			"/api/identities/{identity_id}/federated-credentials",
			{ params: { path: { identity_id: "identity-a" } } },
		);
	});
	it.each([
		[
			useCreateFederatedCredential,
			"post",
			"/api/identities/{identity_id}/federated-credentials",
		],
		[
			useUpdateFederatedCredential,
			"patch",
			"/api/identities/{identity_id}/federated-credentials/{credential_id}",
		],
		[
			useDeleteFederatedCredential,
			"delete",
			"/api/identities/{identity_id}/federated-credentials/{credential_id}",
		],
	] as const)("refreshes credentials after %s", (hook, method, path) => {
		hook();
		expect(mutation.mock.calls[0].slice(0, 2)).toEqual([method, path]);
		mutation.mock.calls[0][2].onSuccess();
		expect(invalidateQueries).toHaveBeenCalledWith({
			queryKey: [
				"get",
				"/api/identities/{identity_id}/federated-credentials",
			],
		});
	});
});
