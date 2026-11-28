import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type FederatedCredential =
	components["schemas"]["FederatedCredentialPublic"];

const COLLECTION = "/api/identities/{identity_id}/federated-credentials";
const ITEM =
	"/api/identities/{identity_id}/federated-credentials/{credential_id}";

export function useFederatedCredentials(identityId: string) {
	return $api.useQuery("get", COLLECTION, {
		params: { path: { identity_id: identityId } },
	});
}

export function useCreateFederatedCredential() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", COLLECTION, {
		onSuccess: () =>
			queryClient.invalidateQueries({ queryKey: ["get", COLLECTION] }),
	});
}

export function useUpdateFederatedCredential() {
	const queryClient = useQueryClient();
	return $api.useMutation("patch", ITEM, {
		onSuccess: () =>
			queryClient.invalidateQueries({ queryKey: ["get", COLLECTION] }),
	});
}

export function useDeleteFederatedCredential() {
	const queryClient = useQueryClient();
	return $api.useMutation("delete", ITEM, {
		onSuccess: () =>
			queryClient.invalidateQueries({ queryKey: ["get", COLLECTION] }),
	});
}
