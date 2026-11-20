import { useQueryClient } from "@tanstack/react-query";

import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type IdentityProvider =
	components["schemas"]["IdentityProviderResponse"];
export type IdentityProviderVendor = IdentityProvider["vendor"];
export type IdentityProviderToken =
	components["schemas"]["IdentityProviderTokenInfo"];
export type IdentityProviderTokenIssued =
	components["schemas"]["IdentityProviderTokenIssued"];
export type IdentityProviderGroup =
	components["schemas"]["IdentityProviderGroupResponse"];
export type OrganizationDomain =
	components["schemas"]["OrganizationDomainResponse"];
export type TokenLifetimeDays =
	components["schemas"]["IdentityProviderTokenRotate"]["lifetime_days"];

/** Where the Identity Providers list lives, under the Users views. */
export const IDENTITY_PROVIDERS_PATH = "/users/identity-providers";

/** Prefix of every identity-provider query key. */
export const IDENTITY_PROVIDERS_QUERY_KEY = [
	"get",
	"/api/identity-providers",
] as const;
const DETAIL_KEY = ["get", "/api/identity-providers/{identity_provider_id}"];
const GROUPS_KEY = [
	"get",
	"/api/identity-providers/{identity_provider_id}/groups",
];
const DOMAINS_KEY = ["get", "/api/identity-providers/domains"];
const USERS_KEY = ["get", "/api/users"];

/** Tokens close to expiry are flagged this many days ahead. */
export const EXPIRY_WARNING_DAYS = 14;
export const TOKEN_LIFETIMES: TokenLifetimeDays[] = [90, 180, 365, 730];

const VENDOR_LABELS: Record<IdentityProviderVendor, string> = {
	entra: "Microsoft Entra ID",
	okta: "Okta",
	jumpcloud: "JumpCloud",
	google: "Google Workspace",
	other: "Other SCIM 2.0",
};

export const VENDORS = Object.keys(VENDOR_LABELS) as IdentityProviderVendor[];

export function vendorLabel(vendor: IdentityProviderVendor): string {
	return VENDOR_LABELS[vendor];
}

export type CredentialStatus = "none" | "active" | "expiring" | "expired";

/** Whether the connection's token works, and whether it needs replacing soon. */
export function credentialStatus(
	token: Pick<IdentityProviderToken, "expires_at"> | null | undefined,
	now: Date = new Date(),
): CredentialStatus {
	if (!token) return "none";
	const remaining = new Date(token.expires_at).getTime() - now.getTime();
	if (remaining <= 0) return "expired";
	if (remaining <= EXPIRY_WARNING_DAYS * 24 * 60 * 60 * 1000) {
		return "expiring";
	}
	return "active";
}

/** The connection that manages a user, or "Native" for one Bifrost manages. */
export function identitySourceLabel(
	user: Pick<components["schemas"]["UserPublic"], "identity_source">,
): string {
	return user.identity_source?.name ?? "Native";
}

/** "2/3": mapped groups out of the groups the provider has pushed. */
export function mappedGroupsLabel(
	provider: Pick<IdentityProvider, "mapped_group_count" | "group_count">,
): string {
	return `${provider.mapped_group_count}/${provider.group_count}`;
}

export function useIdentityProviders() {
	return $api.useQuery("get", "/api/identity-providers");
}

export function useIdentityProvider(identityProviderId: string) {
	return $api.useQuery(
		"get",
		"/api/identity-providers/{identity_provider_id}",
		{ params: { path: { identity_provider_id: identityProviderId } } },
	);
}

export function useIdentityProviderGroups(identityProviderId: string) {
	return $api.useQuery(
		"get",
		"/api/identity-providers/{identity_provider_id}/groups",
		{ params: { path: { identity_provider_id: identityProviderId } } },
	);
}

export function useOrganizationDomains(
	organizationId: string,
	options: { enabled?: boolean } = {},
) {
	return $api.useQuery(
		"get",
		"/api/identity-providers/domains",
		{ params: { query: { organization_id: organizationId } } },
		options,
	);
}

function useInvalidate(...keys: readonly (readonly string[])[]) {
	const queryClient = useQueryClient();
	return () => {
		for (const queryKey of keys) {
			queryClient.invalidateQueries({ queryKey: [...queryKey] });
		}
	};
}

/** Creates a connection; the response carries its first token, shown once. */
export function useCreateIdentityProvider() {
	const onSuccess = useInvalidate(IDENTITY_PROVIDERS_QUERY_KEY);
	return $api.useMutation("post", "/api/identity-providers", { onSuccess });
}

export function useUpdateIdentityProvider() {
	const onSuccess = useInvalidate(IDENTITY_PROVIDERS_QUERY_KEY, DETAIL_KEY);
	return $api.useMutation(
		"patch",
		"/api/identity-providers/{identity_provider_id}",
		{ onSuccess },
	);
}

/** Deleting a connection leaves its users as Native accounts. */
export function useDeleteIdentityProvider() {
	const onSuccess = useInvalidate(IDENTITY_PROVIDERS_QUERY_KEY, USERS_KEY);
	return $api.useMutation(
		"delete",
		"/api/identity-providers/{identity_provider_id}",
		{ onSuccess },
	);
}

/** Issues a new token; earlier tokens keep working for a short overlap. */
export function useRotateIdentityProviderToken() {
	const onSuccess = useInvalidate(IDENTITY_PROVIDERS_QUERY_KEY, DETAIL_KEY);
	return $api.useMutation(
		"post",
		"/api/identity-providers/{identity_provider_id}/token",
		{ onSuccess },
	);
}

/** Mapping changes re-evaluate every linked user's roles and access. */
export function useMapIdentityProviderGroup() {
	const onSuccess = useInvalidate(
		GROUPS_KEY,
		DETAIL_KEY,
		IDENTITY_PROVIDERS_QUERY_KEY,
		USERS_KEY,
	);
	return $api.useMutation(
		"put",
		"/api/identity-providers/{identity_provider_id}/groups/{group_id}/mapping",
		{ onSuccess },
	);
}

export function useUnmapIdentityProviderGroup() {
	const onSuccess = useInvalidate(
		GROUPS_KEY,
		DETAIL_KEY,
		IDENTITY_PROVIDERS_QUERY_KEY,
		USERS_KEY,
	);
	return $api.useMutation(
		"delete",
		"/api/identity-providers/{identity_provider_id}/groups/{group_id}/mapping",
		{ onSuccess },
	);
}

export function useAddOrganizationDomain() {
	const onSuccess = useInvalidate(DOMAINS_KEY);
	return $api.useMutation("post", "/api/identity-providers/domains", {
		onSuccess,
	});
}

/** Checks DNS now; a verified claim also shows on every connection. */
export function useVerifyOrganizationDomain() {
	const onSuccess = useInvalidate(
		DOMAINS_KEY,
		IDENTITY_PROVIDERS_QUERY_KEY,
		DETAIL_KEY,
	);
	return $api.useMutation(
		"post",
		"/api/identity-providers/domains/{claim_id}/verify",
		{ onSuccess },
	);
}

export function useDeleteOrganizationDomain() {
	const onSuccess = useInvalidate(
		DOMAINS_KEY,
		IDENTITY_PROVIDERS_QUERY_KEY,
		DETAIL_KEY,
	);
	return $api.useMutation(
		"delete",
		"/api/identity-providers/domains/{claim_id}",
		{ onSuccess },
	);
}
