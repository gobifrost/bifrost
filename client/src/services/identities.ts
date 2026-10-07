import { useQueryClient } from "@tanstack/react-query";

import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";
import { USER_ACCESS_QUERY_KEY } from "@/services/access";

export type Identity = components["schemas"]["IdentityPublic"];
export type IdentityKind = Identity["identity_kind"];

/** Prefix of every identities-list query key. */
export const IDENTITIES_QUERY_KEY = ["get", "/api/identities"] as const;

const KIND_LABELS: Record<IdentityKind, string> = {
	org_default: "Default",
	global_default: "Global",
	custom: "Custom",
};

/** "Default", "Global" or "Custom". */
export function identityKindLabel(kind: IdentityKind): string {
	return KIND_LABELS[kind];
}

/** Where an identity belongs: its organization's name, or "Global". */
export function identityOrganization(
	identity: Pick<Identity, "organization_name">,
): string {
	return identity.organization_name ?? "Global";
}

/**
 * "Default Identity · Contoso": an identity named in text. Every default
 * identity has the same name, so its organization always comes with it.
 */
export function identityLabel(
	identity: Pick<Identity, "name" | "organization_name">,
): string {
	return `${identity.name} · ${identityOrganization(identity)}`;
}

/** The identities the caller can read, the global identity first. */
export function useIdentities() {
	return $api.useQuery("get", "/api/identities");
}

/** A custom identity with the User base role, in an organization or Global. */
export function useCreateIdentity() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/identities", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: IDENTITIES_QUERY_KEY });
		},
	});
}

export function useRenameIdentity() {
	const queryClient = useQueryClient();
	return $api.useMutation("patch", "/api/identities/{identity_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: IDENTITIES_QUERY_KEY });
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/users/{user_id}"],
			});
			queryClient.invalidateQueries({ queryKey: USER_ACCESS_QUERY_KEY });
		},
	});
}

/** Deletes a custom identity; refused (409, naming them) while workflows run as it. */
export function useDeleteIdentity() {
	const queryClient = useQueryClient();
	return $api.useMutation("delete", "/api/identities/{identity_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: IDENTITIES_QUERY_KEY });
		},
	});
}
